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
    SyncLog,
    SyncLogAction,
)
from app.redis_client import redis
from app.worker_pool import get_arq_pool

logger = structlog.get_logger()
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

DEDUPE_TTL_SECONDS = 86_400


def _verify_bling_signature(body: bytes, header: str | None) -> None:
    s = get_settings()
    secret = (s.bling_webhook_secret or "").encode()
    if not secret:
        raise HTTPException(503, detail={"code": "webhook_secret_missing"})
    if not header:
        raise HTTPException(401, detail={"code": "missing_signature"})
    sig = header.strip()
    if sig.startswith("sha256="):
        sig = sig[len("sha256=") :]
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(401, detail={"code": "bad_signature"})


def _extract_payload(parsed: dict[str, Any]) -> tuple[str | None, int | None, int | None, int | None]:
    """Return (sku, bling_product_id, stock, bling_store_id) — best effort."""
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
        for k in ("saldoVirtualTotal", "disponivel", "saldoFisicoTotal", "quantidade"):
            v = estoque.get(k)
            if v is not None:
                try:
                    stock = int(v)
                    break
                except (TypeError, ValueError):
                    pass

    bling_store_id: int | None = None
    loja = dados.get("loja") or {}
    if isinstance(loja, dict) and loja.get("id") is not None:
        try:
            bling_store_id = int(loja["id"])
        except (TypeError, ValueError):
            bling_store_id = None

    return sku, bling_product_id, stock, bling_store_id


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

    pool = await get_arq_pool()
    arq = await pool.enqueue_job(
        "ingest_bling_order_run",
        bling_order_id,
        str(user_id),
        event,
    )
    logger.info(
        "bling_pedido_webhook_accepted",
        bling_event=event,
        bling_order_id=bling_order_id,
        arq_job_id=arq.job_id if arq is not None else None,
        delivery_id=delivery_key,
    )
    return {
        "ack": True,
        "kind": "pedido",
        "bling_order_id": bling_order_id,
        "delivery_id": delivery_key,
    }


@router.post("/bling")
async def receive_bling_webhook(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_bling_signature: Annotated[str | None, Header(alias="X-Bling-Signature")] = None,
    x_bling_event: Annotated[str | None, Header(alias="X-Bling-Event")] = None,
    x_bling_delivery: Annotated[str | None, Header(alias="X-Bling-Delivery")] = None,
) -> dict[str, Any]:
    body = await request.body()
    _verify_bling_signature(body, x_bling_signature)

    delivery_key = x_bling_delivery or hashlib.sha256(body).hexdigest()
    if not await _claim_delivery(delivery_key):
        return {"ack": True, "duplicate": True, "delivery_id": delivery_key}

    try:
        parsed = json.loads(body or b"{}")
        if not isinstance(parsed, dict):
            parsed = {}
    except json.JSONDecodeError:
        return {"ack": True, "ignored": "invalid_json"}

    if x_bling_event and x_bling_event.startswith("pedido."):
        return await _handle_pedido_event(
            session,
            parsed=parsed,
            event=x_bling_event,
            delivery_key=delivery_key,
        )

    sku, bling_product_id, stock, bling_store_id = _extract_payload(parsed)
    product = await _resolve_product(
        session, sku=sku, bling_product_id=bling_product_id
    )

    if product is None:
        # No matching product — Bling may emit events for products we never imported.
        # Pick any user owning a Bling integration to attribute the log; fallback skip.
        anchor_link = (
            await session.execute(
                select(ProductLink).where(
                    ProductLink.platform == IntegrationPlatform.BLING
                ).limit(1)
            )
        ).scalar_one_or_none()
        if anchor_link is not None:
            session.add(
                SyncLog(
                    user_id=anchor_link.user_id,
                    platform=IntegrationPlatform.BLING,
                    action=SyncLogAction.WEBHOOK_UNMATCHED,
                    status=LinkSyncStatus.SKIPPED,
                    error_code="webhook_unmatched",
                    payload={
                        "event": x_bling_event,
                        "delivery_id": delivery_key,
                        "sku": sku,
                        "bling_product_id": bling_product_id,
                    },
                )
            )
            await session.commit()
        logger.info(
            "bling_webhook_unmatched",
            bling_event=x_bling_event,
            sku=sku,
            bling_product_id=bling_product_id,
        )
        return {"ack": True, "matched": False, "delivery_id": delivery_key}

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
            "event": x_bling_event,
            "delivery_id": delivery_key,
            "product_id": str(product_id),
            "link_ids": link_ids,
            "stock": stock,
            "bling_store_id": bling_store_id,
        },
    )
    session.add(job)
    await session.flush()

    pool = await get_arq_pool()
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
        bling_event=x_bling_event,
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
