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
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    DmConta,
    DmConversa,
    DmMensagem,
    IntegrationPlatform,
    LinkSyncStatus,
    Product,
    ProductLink,
    RedeSocial,
    StockMovement,
    SyncLog,
    SyncLogAction,
)
from app.models.instagram_dm import (
    CONVERSA_HUMANO,
    DIRECAO_ECO,
    DIRECAO_RECEBIDA,
    MSG_DESCARTADA,
    MSG_EM_VOO,
    MSG_RECEBIDA,
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
    reason: str,
    snapshot: dict[str, Any] | None = None,
    *,
    contador: str = SIG_FAIL_COUNTER_KEY,
    snapshot_key: str = SIG_FAIL_SNAPSHOT_KEY,
) -> None:
    """Conta falha de assinatura. A CHAVE é parâmetro de propósito.

    O contador do Bling é o que avisa quando o estoque de 8 mil pedidos/mês
    para de sincronizar. Se a rota da Meta escrevesse nele, qualquer um com
    um curl envenenaria esse alarme sem precisar de segredo nenhum.
    """
    try:
        await redis.incr(contador)
        await redis.expire(contador, SIG_FAIL_COUNTER_TTL)
        if snapshot is not None:
            await redis.set(
                snapshot_key,
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
    # BYTES: com str não-ASCII o compare_digest levanta TypeError e o
    # webhook devolve 500 em vez de seguir. Mesmo defeito da rota da Meta.
    if hmac.compare_digest(expected.encode(), sig.encode("latin-1", "ignore")):
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

    # Bling renomeou o código do produto? Quando o match veio por
    # bling_product_id/link (não por SKU), o payload traz o codigo NOVO e a
    # linha local ainda tem o antigo — espelha aqui, senão o SKU velho vira
    # fantasma duplicado no Controle de Estoque e o novo "não existe" nas
    # devoluções (caso a017.pi → a017.cd, 13/ago/2026).
    if (
        product is not None
        and sku
        and bling_product_id is not None
        and (product.sku or "").strip() != sku
    ):
        logger.info(
            "bling_webhook_sku_renamed",
            old=product.sku,
            new=sku,
            bling_product_id=bling_product_id,
        )
        product.sku = sku

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


# ─────────────────────────── DM do Instagram ───────────────────────────
# Espelho invertido do webhook do Bling: lá o risco de spoof é baixo porque o
# handler só age sobre payload que resolve num Product nosso; aqui o handler
# alimenta um robô que MANDA MENSAGEM NO NOME DA MARCA. Por isso a assinatura
# é DURA — sem segredo configurado ou sem assinatura válida, 403 e pronto.
#
# A Meta exige 200 em até 5 segundos e desativa a assinatura depois de
# algumas falhas seguidas. Então, depois que a assinatura passa: grava,
# enfileira e devolve 200 SEMPRE — inclusive para conta desconhecida e JSON
# inválido. Nada de lógica de negócio aqui dentro.

META_SIG_FAIL_KEY = "webhook:meta:sig_fail_count"
META_SIG_FAIL_SNAPSHOT_KEY = "webhook:meta:sig_fail_last"


def _verify_meta_signature(body: bytes, header: str | None) -> bool:
    """HMAC-SHA256 do corpo CRU com a chave secreta do app.

    O corpo cru importa: a Meta assina os bytes que enviou, com os não-ASCII
    já escapados. Reserializar o JSON (`json.dumps`) quebra a assinatura de
    forma intermitente — e em português, com acento e emoji, quebra sempre.
    """
    s = get_settings()
    # A do app do Instagram primeiro: é ela que assina o objeto `instagram`.
    secret = (s.instagram_app_secret or s.meta_app_secret or "").encode()
    if not secret or not header:
        return False
    sig = header.strip()
    if not sig.startswith("sha256="):
        return False
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    # BYTES, não str: `compare_digest` com str não-ASCII levanta TypeError,
    # e header é decodificado em latin-1 — um curl com byte alto viraria 500
    # em vez de 403, com traceback no log.
    return hmac.compare_digest(
        expected.encode(), sig[len("sha256=") :].encode("latin-1", "ignore")
    )


async def _conta_do_evento(session: AsyncSession, ig_user_id: str) -> DmConta | None:
    """Qual das 4 marcas recebeu a mensagem.

    O payload traz o id do Instagram da conta, não a marca. `external_user_id`
    do token é justamente esse id — é por ele que se resolve, nunca por @.
    """
    return await session.scalar(select(DmConta).where(DmConta.ig_user_id == ig_user_id))


def _dados_da_mensagem(ev: dict[str, Any]) -> dict[str, Any] | None:
    """Achata um item de `entry[].messaging[]` no que a gente guarda.

    Devolve None para evento que não é mensagem (entrega, leitura), que não
    interessa e não deve virar linha.
    """
    msg = ev.get("message")
    if isinstance(msg, dict):
        anexos = msg.get("attachments") or []
        primeiro = anexos[0] if isinstance(anexos, list) and anexos else {}
        payload_anexo = primeiro.get("payload") if isinstance(primeiro, dict) else {}
        if msg.get("is_deleted"):
            tipo = "apagada"
        elif anexos:
            tipo = "anexo"
        elif ev.get("message_reply_to") or msg.get("reply_to"):
            tipo = "story_reply"
        else:
            tipo = "texto"
        return {
            "mid": msg.get("mid"),
            # `is_echo` é a mensagem que a PRÓPRIA conta enviou voltando pelo
            # mesmo webhook. Sem separar isso, o robô responde a si mesmo.
            "direcao": DIRECAO_ECO if msg.get("is_echo") else DIRECAO_RECEBIDA,
            "tipo": tipo,
            "texto": msg.get("text"),
            "anexo_tipo": (primeiro.get("type") if isinstance(primeiro, dict) else None),
            "anexo_url": (
                payload_anexo.get("url") if isinstance(payload_anexo, dict) else None
            ),
            "apagada": bool(msg.get("is_deleted")),
        }
    reacao = ev.get("reaction")
    if isinstance(reacao, dict):
        return {
            "mid": reacao.get("mid"),
            "direcao": DIRECAO_RECEBIDA,
            "tipo": "reacao",
            "texto": reacao.get("emoji"),
            "anexo_tipo": None,
            "anexo_url": None,
            "apagada": False,
        }
    postback = ev.get("postback")
    if isinstance(postback, dict):
        return {
            "mid": postback.get("mid"),
            "direcao": DIRECAO_RECEBIDA,
            "tipo": "postback",
            "texto": postback.get("title") or postback.get("payload"),
            "anexo_tipo": None,
            "anexo_url": None,
            "apagada": False,
        }
    return None


@router.get("/meta/instagram")
async def verify_meta_webhook(request: Request) -> Response:
    """Handshake da Meta.

    Devolve o `hub.challenge` como TEXTO PURO. Retornar o valor direto de uma
    rota FastAPI o serializaria em JSON (`"123"`, com aspas) e a Meta recusa a
    URL — é a causa nº 1 de "The URL couldn't be validated".
    """
    s = get_settings()
    p = request.query_params
    esperado = s.meta_webhook_verify_token or ""
    recebido = p.get("hub.verify_token") or ""
    if (
        esperado
        and p.get("hub.mode") == "subscribe"
        # BYTES pelo mesmo motivo: `?hub.verify_token=ção` viraria 500 — e é
        # justamente este GET que a Meta chama pra validar a URL.
        and hmac.compare_digest(esperado.encode(), recebido.encode("utf-8", "ignore"))
    ):
        logger.info("meta_webhook_verificado")
        return Response(content=p.get("hub.challenge") or "", media_type="text/plain")
    logger.warning("meta_webhook_verify_recusado", tem_segredo=bool(esperado))
    return Response(status_code=status.HTTP_403_FORBIDDEN)


@router.post("/meta/instagram")
async def receive_meta_webhook(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_hub_signature_256: Annotated[str | None, Header(alias="X-Hub-Signature-256")] = None,
) -> dict[str, Any]:
    body = await request.body()
    if not _verify_meta_signature(body, x_hub_signature_256):
        await _bump_sig_failure(
            "meta_bad_signature",
            {"body_len": len(body)},
            contador=META_SIG_FAIL_KEY,
            snapshot_key=META_SIG_FAIL_SNAPSHOT_KEY,
        )
        logger.warning("meta_webhook_sig_invalida", body_len=len(body))
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="assinatura inválida")

    try:
        parsed = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return {"ack": True, "ignored": "invalid_json"}
    if not isinstance(parsed, dict) or parsed.get("object") != "instagram":
        return {"ack": True, "ignored": "objeto_inesperado"}

    gravadas = 0
    # Teto nos dois laços: mesmo com assinatura válida, um payload gigante
    # viraria um commit por item sem limite.
    for entry in (parsed.get("entry") or [])[:200]:
        if not isinstance(entry, dict):
            continue
        for ev in (entry.get("messaging") or [])[:200]:
            if not isinstance(ev, dict):
                continue
            dados = _dados_da_mensagem(ev)
            if not dados or not dados.get("mid"):
                continue

            # Quem é a CONTA depende da direção: no eco, a conta é quem mandou.
            sender = (ev.get("sender") or {}).get("id")
            recipient = (ev.get("recipient") or {}).get("id")
            eco = dados["direcao"] == DIRECAO_ECO
            ig_conta = sender if eco else recipient
            igsid = recipient if eco else sender
            if not ig_conta or not igsid:
                continue

            try:
                await _gravar_dm(
                    session,
                    ig_conta=str(ig_conta),
                    igsid=str(igsid),
                    dados=dados,
                    ev=ev,
                )
                gravadas += 1
            except Exception as e:  # noqa: BLE001
                # Nunca deixar uma mensagem ruim derrubar o 200: a Meta
                # desativa a assinatura depois de falhas seguidas, e aí para
                # de chegar DM em silêncio.
                await session.rollback()
                # `str(e)` do SQLAlchemy inclui [SQL: ...] e [parameters: ...] — ou seja,
                # o texto da DM iria pro log. Só o tipo.
                logger.warning(
                    "meta_webhook_grava_falhou",
                    err=type(e).__name__,
                    mid=dados.get("mid"),
                )

    return {"ack": True, "gravadas": gravadas}


async def _gravar_dm(
    session: AsyncSession,
    *,
    ig_conta: str,
    igsid: str,
    dados: dict[str, Any],
    ev: dict[str, Any],
) -> None:
    token = await _conta_do_evento(session, ig_conta)
    if token is None:
        # Conta que não é nossa (ou token ainda não conectado). Não é erro:
        # loga e segue — 200 mesmo assim.
        logger.info("meta_webhook_conta_desconhecida", ig_conta=ig_conta)
        return

    ts = ev.get("timestamp")
    ocorrido = (
        datetime.fromtimestamp(ts / 1000, tz=UTC)
        if isinstance(ts, int | float) and ts > 0
        else datetime.now(UTC)
    )

    conversa = await session.scalar(
        select(DmConversa).where(
            DmConversa.rede_social_id == token.rede_social_id,
            DmConversa.participante_id == igsid,
        )
    )
    if conversa is None:
        # Snapshot do @ da marca, como MarketingPostagem faz: apagar a conta
        # não pode deixar o histórico sem saber de quem era a conversa.
        rede = await session.get(RedeSocial, token.rede_social_id)
        conversa = DmConversa(
            rede_social_id=token.rede_social_id,
            plataforma="instagram",
            conta=(rede.conta if rede else None),
            participante_id=igsid,
        )
        session.add(conversa)
        await session.flush()

    if dados["direcao"] == DIRECAO_RECEBIDA:
        conversa.ultima_recebida_em = ocorrido
    else:
        conversa.ultima_enviada_em = ocorrido
        # Humano (ou outro app) respondeu pela caixa de entrada: o robô se
        # cala nesta conversa. Responder por cima de atendimento em andamento
        # é o jeito mais rápido de passar vergonha.
        conversa.auto = False
        conversa.status = CONVERSA_HUMANO

    # APAGADA ("unsent"): o evento chega com o MESMO `mid` da mensagem
    # original. Não é INSERT — morreria no UNIQUE e seria confundido com
    # reentrega. É UPDATE que ESVAZIA o conteúdo: guardar o texto que a
    # pessoa apagou é o oposto do que ela pediu. A Meta exige que a gravação
    # do App Review mostre esse tratamento.
    if dados["apagada"]:
        anterior = await session.scalar(
            select(DmMensagem).where(DmMensagem.mid == dados["mid"])
        )
        if anterior is not None:
            anterior.tipo = "apagada"
            anterior.texto = None
            anterior.anexo_url = None
            anterior.payload = {}
            anterior.apagada_em = ocorrido
            # Resposta que ainda não saiu perde o sentido: responder a uma
            # mensagem apagada é responder ao que não existe mais.
            pendentes = await session.scalars(
                select(DmMensagem).where(
                    DmMensagem.conversa_id == conversa.id,
                    DmMensagem.status.in_(MSG_EM_VOO),
                )
            )
            for pendente in pendentes:
                pendente.status = MSG_DESCARTADA
                pendente.motivo = "cliente apagou a mensagem"
            await session.commit()
            logger.info("meta_webhook_apagada", mid=dados["mid"])
            return
        # Apagamento chegou sem a original (webhook fora de ordem): grava a
        # lápide mesmo assim, pro histórico não mentir que nunca existiu.

    session.add(
        DmMensagem(
            conversa_id=conversa.id,
            mid=dados["mid"],
            direcao=dados["direcao"],
            tipo=dados["tipo"],
            texto=dados["texto"],
            anexo_tipo=dados["anexo_tipo"],
            anexo_url=dados["anexo_url"],
            payload=ev,
            ocorrido_em=ocorrido,
            apagada_em=ocorrido if dados["apagada"] else None,
            status=MSG_RECEBIDA,
        )
    )
    try:
        await session.commit()
    except IntegrityError:
        # `mid` UNIQUE: é a reentrega da Meta chegando de novo. Rotina de
        # deploy, não exceção — a api reiniciando devolve não-200 e ela
        # retenta por horas.
        await session.rollback()
        logger.info("meta_webhook_reentrega", mid=dados["mid"])
