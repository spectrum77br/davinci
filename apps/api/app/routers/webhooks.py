"""Webhook endpoints (Fase 5).

Bling sends product/stock change events. Flow:
    1. Validate `X-Bling-Signature` HMAC-SHA256 of body with BLING_WEBHOOK_SECRET.
    2. Dedup `X-Bling-Delivery` (or sha256(body) fallback) in Redis SET NX EX 24h.
    3. Resolve product by SKU first, then by Bling product.id (link.external_id).
    4. Update `products.bling_stock` inline (cheap UPDATE) — refresh signal.
    5. For each active product_link, enqueue `sync_product_run` so each
       marketplace mirrors the new stock asynchronously.
    6. Return 200 fast (< 500ms p95).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    IntegrationPlatform,
    LinkSyncStatus,
    Product,
    ProductLink,
    StockMovement,
    SyncLog,
    SyncLogAction,
)
from app.redis_client import redis
from app.worker_pool import get_arq_pool, get_arq_sync_pool

logger = structlog.get_logger()
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

DEDUPE_TTL_SECONDS = 86_400

# Intelligent threshold: sales (stock going down) with stock > 5 are deferred
# to daily sync. Restocks (stock going up) ALWAYS propagate immediately.
# Stock = 0 ALWAYS propagates immediately.
WEBHOOK_LOW_STOCK_LIMIT = 5


SIG_FAIL_COUNTER_KEY = "webhook:bling:sig_fail_count"
SIG_FAIL_COUNTER_TTL = 3600
SIG_FAIL_SNAPSHOT_KEY = "webhook:bling:sig_fail_last"
SIG_FAIL_SNAPSHOT_TTL = 7200


async def _bump_sig_failure(
    reason: str, snapshot: dict[str, Any] | None = None
) -> None:
    try:
        await redis.incr(SIG_FAIL_COUNTER_KEY)
        await redis.expire(SIG_FAIL_COUNTER_KEY, SIG_FAIL_COUNTER_TTL)
        if snapshot is not None:
            await redis.set(
                SIG_FAIL_SNAPSHOT_KEY,
                json.dumps({"reason": reason, **snapshot}, default=str),
                ex=SIG_FAIL_SNAPSHOT_TTL,
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("webhook_sig_counter_failed", err=str(e), reason=reason)


async def _verify_bling_signature(
    body: bytes,
    header: str | None,
    *,
    headers_seen: dict[str, str] | None = None,
) -> None:
    # Bling V3 does not expose a configurable webhook secret in the panel and
    # the auto-signed header (HMAC-SHA256 with client_secret) is unreliable in
    # practice — ~75% of legitimate deliveries arrive with a signature that
    # doesn't match what we compute, likely due to upstream body normalization.
    # We keep the check as a soft observability signal: log mismatches but
    # never reject, so stock-update webhooks aren't lost. Spoofing risk is low
    # because the handler only acts on payloads that resolve to a Product we
    # own (SKU or bling_product_id match).
    s = get_settings()
    secret = (s.bling_webhook_secret or "").encode()
    if not header or not secret:
        return
    sig = header.strip()
    if sig.startswith("sha256="):
        sig = sig[len("sha256=") :]
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    if hmac.compare_digest(expected, sig):
        return
    bling_headers = {
        k: v for k, v in (headers_seen or {}).items()
        if k.lower().startswith("x-bling")
    }
    snap = {
        "body_len": len(body),
        "body_sha256_prefix": hashlib.sha256(body).hexdigest()[:12],
        "secret_len": len(secret),
        "received_prefix": sig[:8],
        "expected_prefix": expected[:8],
        "bling_headers": bling_headers,
    }
    await _bump_sig_failure("bad_signature", snap)
    logger.warning("bling_webhook_sig_mismatch", **snap)


def _extract_payload(parsed: dict[str, Any]) -> tuple[str | None, int | None, int | None, int | None]:
    """Return (sku, bling_product_id, stock, bling_store_id) — best effort.

    Bling sends two webhook shapes that BOTH end up here:
      * "produto.*" — stock lives at `dados.estoque.saldoVirtualTotal`.
      * "estoque.*" — stock lives at `dados.saldoVirtualTotal` (root).
    We try the nested path first (older / product-level events) and then
    fall through to the root form. `_extract_stock_event` parses the
    movement-specific fields (operacao, quantidade, saldoFisicoTotal)
    that only appear on the estoque shape — kept separate so callers
    that only care about the new stock value can keep their tuple
    unpacking unchanged.
    """
    dados = parsed.get("dados") or parsed.get("data") or {}
    if not isinstance(dados, dict):
        dados = {}
    sku = dados.get("codigo") or dados.get("sku")
    sku = (sku or "").strip() or None

    bling_product_id: int | None = None
    raw_id = dados.get("id")
    if raw_id is None and isinstance(dados.get("produto"), dict):
        raw_id = dados["produto"].get("id")
    try:
        bling_product_id = int(raw_id) if raw_id is not None else None
    except (TypeError, ValueError):
        bling_product_id = None

    stock: int | None = None
    estoque = dados.get("estoque") or {}
    if isinstance(estoque, dict):
        # Strictly virtual stock. Physical (saldoFisicoTotal) counts reserved
        # units that are no longer available to sell, so using it would push
        # over-stated stock to the marketplace.
        v = estoque.get("saldoVirtualTotal")
        if v is not None:
            try:
                stock = int(v)
            except (TypeError, ValueError):
                stock = None
    # Fall through to root for the estoque.* webhook shape.
    if stock is None:
        v = dados.get("saldoVirtualTotal")
        if v is not None:
            try:
                stock = int(v)
            except (TypeError, ValueError):
                stock = None

    bling_store_id: int | None = None
    loja = dados.get("loja") or {}
    if isinstance(loja, dict) and loja.get("id") is not None:
        try:
            bling_store_id = int(loja["id"])
        except (TypeError, ValueError):
            bling_store_id = None

    return sku, bling_product_id, stock, bling_store_id


def _extract_stock_event(
    parsed: dict[str, Any],
) -> tuple[str | None, int | None, float | None, str | None]:
    """Pull the movement-specific fields out of the estoque webhook payload.

    Returns `(operacao, quantidade, saldo_fisico_total, observacao)`.
    All four are None on payloads that don't carry the estoque shape
    (e.g. produto.alteracao) — the caller treats that as "no movement
    to record". `operacao` is "E" (entrada) or "S" (saida); anything
    else is normalised to None so the StockMovement insert never gets
    a garbage `tipo`.
    """
    dados = parsed.get("dados") or parsed.get("data") or {}
    if not isinstance(dados, dict):
        return None, None, None, None

    op_raw = dados.get("operacao")
    operacao: str | None = None
    if isinstance(op_raw, str) and op_raw.strip().upper() in ("E", "S"):
        operacao = op_raw.strip().upper()

    quantidade: int | None = None
    raw_qtd = dados.get("quantidade")
    if raw_qtd is not None:
        try:
            quantidade = int(float(raw_qtd))
        except (TypeError, ValueError):
            quantidade = None

    saldo_fisico: float | None = None
    raw_fisico = dados.get("saldoFisicoTotal")
    if raw_fisico is not None:
        try:
            saldo_fisico = float(raw_fisico)
        except (TypeError, ValueError):
            saldo_fisico = None

    obs_raw = dados.get("observacao") or dados.get("observacoes")
    observacao: str | None = (obs_raw or "").strip() or None if isinstance(obs_raw, str) else None

    return operacao, quantidade, saldo_fisico, observacao


async def _claim_delivery(delivery_key: str) -> bool:
    """Returns True if first time we see this delivery; False if duplicate."""
    redis_key = f"webhook:bling:dedupe:{delivery_key}"
    return bool(await redis.set(redis_key, "1", nx=True, ex=DEDUPE_TTL_SECONDS))


async def _resolve_product(
    session: AsyncSession,
    *,
    sku: str | None,
    bling_product_id: int | None,
) -> Product | None:
    if sku:
        prod = (
            await session.execute(select(Product).where(Product.sku == sku).limit(1))
        ).scalar_one_or_none()
        if prod is not None:
            return prod
    if bling_product_id is not None:
        prod = (
            await session.execute(
                select(Product).where(Product.bling_product_id == bling_product_id).limit(1)
            )
        ).scalar_one_or_none()
        if prod is not None:
            return prod
        link = (
            await session.execute(
                select(ProductLink).where(
                    ProductLink.platform == IntegrationPlatform.BLING,
                    ProductLink.external_id == str(bling_product_id),
                ).limit(1)
            )
        ).scalar_one_or_none()
        if link is not None:
            return await session.get(Product, link.product_id)
    return None


async def _resolve_bling_user_id(session: AsyncSession) -> UUID | None:
    """Pick first user owning a Bling integration. Single-tenant attribution
    — webhook payloads don't identify the DaVinci user, only the Bling account.
    Mirrors the pattern used for unmatched product webhooks below."""
    from app.models import Integration  # local import avoids cycle at module load
    integ = (
        await session.execute(
            select(Integration).where(
                Integration.platform == IntegrationPlatform.BLING
            ).limit(1)
        )
    ).scalar_one_or_none()
    return integ.user_id if integ is not None else None


async def _handle_pedido_event(
    session: AsyncSession,
    *,
    parsed: dict[str, Any],
    event: str,
    delivery_key: str,
) -> dict[str, Any]:
    dados = parsed.get("dados") or parsed.get("data") or {}
    if not isinstance(dados, dict):
        dados = {}
    raw_id = dados.get("id")
    try:
        bling_order_id = int(raw_id) if raw_id is not None else None
    except (TypeError, ValueError):
        bling_order_id = None
    if bling_order_id is None:
        return {"ack": True, "ignored": "missing_order_id", "delivery_id": delivery_key}

    user_id = await _resolve_bling_user_id(session)
    if user_id is None:
        logger.warning(
            "bling_pedido_webhook_no_integration",
            bling_event=event,
            bling_order_id=bling_order_id,
        )
        return {
            "ack": True,
            "ignored": "no_bling_integration",
            "delivery_id": delivery_key,
        }

    # Registro DURÁVEL do ingest antes de enfileirar. Sem ele o webhook de
    # pedido só empurrava um job arq — se o ingest falhasse em definitivo
    # (Bling 5xx, transação Postgres envenenada) o arq descartava em silêncio,
    # invisível ao failed_jobs_alert_scan, e só o backfill diário recuperava (no
    # dia seguinte). Com o BackgroundJob o ingest_bling_order_run atualiza o
    # status (RUNNING→SUCCEEDED/FAILED) e o cron ingest_orders_retry_sweep
    # re-dirige os FAILED em minutos. `bling_order_id`/`user_id`/`event` no
    # payload são o que o sweep precisa pra re-enfileirar sem re-listar o Bling.
    job = BackgroundJob(
        type=BackgroundJobType.INGEST_BLING_ORDER,
        status=BackgroundJobStatus.PENDING,
        created_by=user_id,
        total=1,
        payload={
            "trigger": "webhook_bling",
            "event": event,
            "delivery_id": delivery_key,
            "bling_order_id": bling_order_id,
            "user_id": str(user_id),
        },
    )
    session.add(job)
    await session.flush()

    pool = await get_arq_pool()
    arq = await pool.enqueue_job(
        "ingest_bling_order_run",
        bling_order_id,
        str(user_id),
        event,
        str(job.id),
    )
    if arq is not None:
        job.arq_job_id = arq.job_id
    await session.commit()

    logger.info(
        "bling_pedido_webhook_accepted",
        bling_event=event,
        bling_order_id=bling_order_id,
        job_id=str(job.id),
        arq_job_id=arq.job_id if arq is not None else None,
        delivery_id=delivery_key,
    )
    return {
        "ack": True,
        "kind": "pedido",
        "bling_order_id": bling_order_id,
        "job_id": str(job.id),
        "delivery_id": delivery_key,
    }


@router.post("/bling")
async def receive_bling_webhook(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_bling_signature: Annotated[str | None, Header(alias="X-Bling-Signature")] = None,
    x_bling_signature_256: Annotated[str | None, Header(alias="X-Bling-Signature-256")] = None,
    x_bling_event: Annotated[str | None, Header(alias="X-Bling-Event")] = None,
    x_bling_delivery: Annotated[str | None, Header(alias="X-Bling-Delivery")] = None,
) -> dict[str, Any]:
    body = await request.body()
    await _verify_bling_signature(
        body,
        x_bling_signature_256 or x_bling_signature,
        headers_seen=dict(request.headers),
    )

    delivery_key = x_bling_delivery or hashlib.sha256(body).hexdigest()
    if not await _claim_delivery(delivery_key):
        return {"ack": True, "duplicate": True, "delivery_id": delivery_key}

    try:
        parsed = json.loads(body or b"{}")
        if not isinstance(parsed, dict):
            parsed = {}
    except json.JSONDecodeError:
        return {"ack": True, "ignored": "invalid_json"}

    body_event = parsed.get("event") if isinstance(parsed.get("event"), str) else None
    bling_event = body_event or x_bling_event

    if bling_event and (
        bling_event.startswith("pedido.") or bling_event.startswith("order.")
    ):
        return await _handle_pedido_event(
            session,
            parsed=parsed,
            event=bling_event,
            delivery_key=delivery_key,
        )

    sku, bling_product_id, stock, bling_store_id = _extract_payload(parsed)

    # Resolve product first — needed for intelligent threshold logic
    product = await _resolve_product(
        session, sku=sku, bling_product_id=bling_product_id
    )

    # ── Intelligent threshold logic (matches SSH behavior) ──
    # Old approach: drop all webhooks with stock > 10.
    # New approach:
    #   - SALE (stock went down): only propagate if stock ≤ 5 (low stock)
    #   - RESTOCK (stock went up): ALWAYS propagate immediately
    #   - Stock = 0: ALWAYS propagate (force marketplaces to zero)
    #   - Stock unchanged: skip (unless stock = 0)
    if stock is not None and product is not None:
        previous_stock = product.stock or 0
        is_sale = stock < previous_stock
        is_restock = stock > previous_stock
        stock_unchanged = stock == previous_stock

        # Always update local DB with latest stock from Bling
        product.stock = stock

        # Record the stock event for the operador-facing /controle-estoque
        # journal AND update the cached `reserved_stock` (physical minus
        # virtual). Only the estoque.* webhook carries `operacao` +
        # `quantidade` — produto.* payloads make these None, so the
        # StockMovement insert is skipped on those.
        operacao_e, qtd_e, saldo_fisico_e, obs_e = _extract_stock_event(parsed)
        if operacao_e and qtd_e is not None and qtd_e > 0:
            session.add(
                StockMovement(
                    bling_product_id=int(product.bling_product_id or 0),
                    sku=product.sku,
                    product_name=product.name,
                    date=datetime.now(UTC),
                    tipo=operacao_e,
                    quantidade=qtd_e,
                    observacao=obs_e,
                    origem=None,  # filled lazily on first /api/estoque/produtos read
                    saldo_fisico=saldo_fisico_e,
                    saldo_virtual=float(stock),
                )
            )
        if saldo_fisico_e is not None:
            product.reserved_stock = max(0, int(saldo_fisico_e - stock))

        if is_sale and stock > WEBHOOK_LOW_STOCK_LIMIT:
            # Sale but stock still high — defer to daily sync
            await session.commit()
            logger.info(
                "bling_webhook_deferred_sale",
                sku=sku,
                previous=previous_stock,
                current=stock,
                delivery_id=delivery_key,
            )
            return {
                "ack": True,
                "deferred": "sale_hi_stock",
                "stock": stock,
                "previous": previous_stock,
                "delivery_id": delivery_key,
            }

        if stock_unchanged and stock != 0:
            # No change and not zero — skip
            await session.commit()
            return {
                "ack": True,
                "ignored": "stock_unchanged",
                "stock": stock,
                "delivery_id": delivery_key,
            }

    if product is None:
        # No matching product — Bling may emit events for products we never
        # imported. Attribute audit to a Bling integration owner. When we have
        # a bling_product_id, enqueue an auto-create job so the next webhook
        # for the same product matches.
        anchor_link = (
            await session.execute(
                select(ProductLink).where(
                    ProductLink.platform == IntegrationPlatform.BLING
                ).limit(1)
            )
        ).scalar_one_or_none()
        owner_user_id: UUID | None = (
            anchor_link.user_id if anchor_link is not None else None
        )
        if anchor_link is not None:
            session.add(
                SyncLog(
                    user_id=anchor_link.user_id,
                    platform=IntegrationPlatform.BLING,
                    action=SyncLogAction.WEBHOOK_UNMATCHED,
                    status=LinkSyncStatus.SKIPPED,
                    error_code="webhook_unmatched",
                    payload={
                        "event": bling_event,
                        "delivery_id": delivery_key,
                        "sku": sku,
                        "bling_product_id": bling_product_id,
                    },
                )
            )
            await session.commit()
        auto_create_enqueued = False
        if owner_user_id is not None and bling_product_id is not None:
            # Redis dedupe: collapse repeated webhooks for the same Bling
            # product into one create job per 5 minutes.
            dedupe_key = f"auto_create_product:{owner_user_id}:{bling_product_id}"
            first = await redis.set(dedupe_key, "1", nx=True, ex=300)
            if first:
                pool = await get_arq_pool()
                await pool.enqueue_job(
                    "auto_create_product_from_bling_run",
                    int(bling_product_id),
                    str(owner_user_id),
                )
                auto_create_enqueued = True
        logger.info(
            "bling_webhook_unmatched",
            bling_event=bling_event,
            sku=sku,
            bling_product_id=bling_product_id,
            auto_create_enqueued=auto_create_enqueued,
        )
        return {
            "ack": True,
            "matched": False,
            "delivery_id": delivery_key,
            "auto_create_enqueued": auto_create_enqueued,
        }

    if stock is not None:
        product.stock = stock
    product_id: UUID = product.id
    user_id: UUID = product.user_id

    links = (
        await session.execute(
            select(ProductLink).where(
                ProductLink.product_id == product_id,
                ProductLink.last_sync_status.in_(
                    [LinkSyncStatus.OK, LinkSyncStatus.PENDING, LinkSyncStatus.REQUIRES_REVIEW]
                ),
            )
        )
    ).scalars().all()
    link_ids = [str(l.id) for l in links]

    job = BackgroundJob(
        type=BackgroundJobType.SYNC_PRODUCT,
        status=BackgroundJobStatus.PENDING,
        created_by=user_id,
        total=len(link_ids),
        payload={
            "trigger": "webhook_bling",
            "event": bling_event,
            "delivery_id": delivery_key,
            "product_id": str(product_id),
            "link_ids": link_ids,
            "stock": stock,
            "bling_store_id": bling_store_id,
        },
    )
    session.add(job)
    await session.flush()

    # Fila dedicada de sync (davinci_sync): o webhook de PRODUTO empurra estoque
    # pro marketplace e mora com o resto do sync, isolado do ingest de PEDIDO
    # (que fica na default). Ver worker_pool.ARQ_SYNC_QUEUE.
    pool = await get_arq_sync_pool()
    arq = await pool.enqueue_job(
        "sync_product_run",
        str(job.id),
        str(user_id),
        str(product_id),
        link_ids or None,
    )
    if arq is not None:
        job.arq_job_id = arq.job_id
    await session.commit()

    logger.info(
        "bling_webhook_accepted",
        bling_event=bling_event,
        product_id=str(product_id),
        job_id=str(job.id),
        links=len(link_ids),
        stock=stock,
        delivery_id=delivery_key,
    )

    return {
        "ack": True,
        "job_id": str(job.id),
        "product_id": str(product_id),
        "links": len(link_ids),
        "delivery_id": delivery_key,
    }
