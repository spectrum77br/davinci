"""Ingest Bling pedidos de venda from API into `bling_orders`.

Triggered by Bling webhook (`pedido.alteracao`, `pedido.alteracao.situacao`,
`pedido.exclusao`). Webhook payload only carries an order id; full order is
fetched via `GET /pedidos/vendas/{id}` and split into one row per item.

`pedido.exclusao` sets `situacao='excluido'` (soft delete — audit trail).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    BlingOrder,
    Integration,
    IntegrationPlatform,
    LinkSyncStatus,
    Product,
    ProductLink,
    Store,
    UserSettings,
)
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketplaces.bling import BlingClient
from app.worker_pool import get_arq_pool

logger = structlog.get_logger()


def _to_dt(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    s = str(raw).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _num(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _int(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


async def _resolve_store_id(
    session: AsyncSession, bling_loja_id: int | None
) -> UUID | None:
    if bling_loja_id is None:
        return None
    row = (
        await session.execute(
            select(Store.id).where(Store.bling_store_id == bling_loja_id).limit(1)
        )
    ).scalar_one_or_none()
    return row


async def _bling_client_for_user(
    session: AsyncSession, user_id: UUID
) -> BlingClient | None:
    integ = (
        await session.execute(
            select(Integration).where(
                and_(
                    Integration.user_id == user_id,
                    Integration.platform == IntegrationPlatform.BLING,
                )
            ).limit(1)
        )
    ).scalar_one_or_none()
    if integ is None:
        return None
    creds = decrypt_json(integ.credentials)

    async def _persist(new_creds: dict) -> None:
        integ.credentials = encrypt_json(new_creds)
        exp = new_creds.get("expires_at")
        if exp:
            integ.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        await session.commit()

    return BlingClient(creds, on_token_refresh=_persist)


def _row_from_item(
    raw_order: dict[str, Any],
    item: dict[str, Any],
    *,
    item_index: int,
    store_id: UUID | None,
) -> dict[str, Any]:
    """Flatten one (order, item) pair into a `bling_orders` row dict."""
    loja = raw_order.get("loja") or {}
    if not isinstance(loja, dict):
        loja = {}
    categoria = (item.get("produto") or {}).get("categoria") or {}
    if not isinstance(categoria, dict):
        categoria = {}
    comissao = item.get("comissao") or {}
    if not isinstance(comissao, dict):
        comissao = {}
    produto = item.get("produto") or {}
    if not isinstance(produto, dict):
        produto = {}

    return {
        "bling_id": _int(raw_order.get("id")),
        "numero": str(raw_order["numero"]) if raw_order.get("numero") is not None else None,
        "numeroloja": (str(raw_order["numeroLoja"]) if raw_order.get("numeroLoja") else None),
        "numero_documento": (
            str(raw_order["numeroPedidoCompra"])
            if raw_order.get("numeroPedidoCompra") else None
        ),
        "data": _to_dt(raw_order.get("data")),
        "totalprodutos": _num(raw_order.get("totalProdutos") or raw_order.get("totalprodutos")),
        "total": _num(raw_order.get("total")),
        "situacao": (
            str(((raw_order.get("situacao") or {}) or {}).get("id"))
            if isinstance(raw_order.get("situacao"), dict)
            else (str(raw_order.get("situacao")) if raw_order.get("situacao") is not None else None)
        ),
        "loja": str(loja.get("id")) if loja.get("id") is not None else None,
        "store_id": store_id,
        "itens": raw_order.get("itens"),
        "valorbase": _num(raw_order.get("valorBase") or raw_order.get("valorbase")),
        "custofrete": _num(raw_order.get("transporte", {}).get("frete") if isinstance(raw_order.get("transporte"), dict) else None),
        "taxacomissao": _num(raw_order.get("taxas", {}).get("taxaComissao") if isinstance(raw_order.get("taxas"), dict) else None),
        "item_id": _int(item.get("id")),
        "item_index": item_index,
        "itemvalor": _num(item.get("valor")),
        "item_codigo": item.get("codigo") or produto.get("codigo"),
        "item_produto_id": _int(produto.get("id")),
        "item_descricao": item.get("descricao") or produto.get("nome"),
        "item_quantidade": _int(item.get("quantidade")),
        "item_desconto": _num(item.get("desconto")),
        "item_comissao_base": _num(comissao.get("base")),
        "item_comissao_valor": _num(comissao.get("valor")),
        "categoria_id": _int(categoria.get("id")),
        "categoria_nome": categoria.get("descricao") or categoria.get("nome"),
    }


async def upsert_order(
    session: AsyncSession,
    raw_order: dict[str, Any],
) -> int:
    """Replace all rows for an order with one row per item. Returns row count."""
    bling_id = _int(raw_order.get("id"))
    if bling_id is None:
        return 0

    loja = raw_order.get("loja") or {}
    bling_loja_id = _int(loja.get("id")) if isinstance(loja, dict) else None
    store_id = await _resolve_store_id(session, bling_loja_id)

    itens = raw_order.get("itens") or []
    if not isinstance(itens, list):
        itens = []

    await session.execute(
        delete(BlingOrder).where(BlingOrder.bling_id == bling_id)
    )

    if not itens:
        # Order with no items — keep one summary row at item_index=0.
        row = _row_from_item(raw_order, {}, item_index=0, store_id=store_id)
        session.add(BlingOrder(**row))
        return 1

    for idx, item in enumerate(itens):
        if not isinstance(item, dict):
            continue
        row = _row_from_item(raw_order, item, item_index=idx, store_id=store_id)
        session.add(BlingOrder(**row))
    return len(itens)


async def mark_order_excluido(
    session: AsyncSession,
    bling_order_id: int,
) -> int:
    """Soft-delete: stamp `situacao='excluido'` across all rows of the order."""
    rows = (
        await session.execute(
            select(BlingOrder).where(BlingOrder.bling_id == bling_order_id)
        )
    ).scalars().all()
    for r in rows:
        r.situacao = "excluido"
    return len(rows)


async def _resolve_local_product(
    session: AsyncSession,
    *,
    user_id: UUID,
    sku: str | None,
    bling_product_id: int | None,
) -> Product | None:
    if sku:
        p = (
            await session.execute(
                select(Product).where(
                    Product.user_id == user_id, Product.sku == sku
                ).limit(1)
            )
        ).scalar_one_or_none()
        if p is not None:
            return p
    if bling_product_id is not None:
        p = (
            await session.execute(
                select(Product).where(
                    Product.user_id == user_id,
                    Product.bling_product_id == bling_product_id,
                ).limit(1)
            )
        ).scalar_one_or_none()
        if p is not None:
            return p
        link = (
            await session.execute(
                select(ProductLink).where(
                    ProductLink.user_id == user_id,
                    ProductLink.platform == IntegrationPlatform.BLING,
                    ProductLink.external_id == str(bling_product_id),
                ).limit(1)
            )
        ).scalar_one_or_none()
        if link is not None:
            return await session.get(Product, link.product_id)
    return None


async def _enqueue_stock_refresh_for_order(
    session: AsyncSession,
    *,
    raw_order: dict[str, Any],
    user_id: UUID,
) -> tuple[list[tuple[UUID, UUID, list[str]]], list[dict[str, Any]]]:
    """For each item in the order, resolve local Product and create a
    SYNC_PRODUCT BackgroundJob so the stock refresh shows up in the jobs UI.
    Returns (jobs_to_enqueue, notif_items)."""
    itens = raw_order.get("itens") or []
    if not isinstance(itens, list):
        return [], []
    seen_products: set[UUID] = set()
    jobs: list[tuple[UUID, UUID, list[str]]] = []
    notif: list[dict[str, Any]] = []
    for item in itens:
        if not isinstance(item, dict):
            continue
        produto = item.get("produto") or {}
        if not isinstance(produto, dict):
            produto = {}
        sku_raw = item.get("codigo") or produto.get("codigo")
        sku = (str(sku_raw).strip() or None) if sku_raw is not None else None
        bling_pid = _int(produto.get("id"))
        qty = _int(item.get("quantidade"))
        desc = item.get("descricao") or produto.get("nome")
        product = await _resolve_local_product(
            session, user_id=user_id, sku=sku, bling_product_id=bling_pid
        )
        notif.append(
            {
                "sku": sku,
                "desc": desc,
                "qty": qty,
                "matched": product is not None,
            }
        )
        if product is None or product.id in seen_products:
            continue
        seen_products.add(product.id)
        links = (
            await session.execute(
                select(ProductLink).where(
                    ProductLink.product_id == product.id,
                    ProductLink.last_sync_status.in_(
                        [
                            LinkSyncStatus.OK,
                            LinkSyncStatus.PENDING,
                            LinkSyncStatus.REQUIRES_REVIEW,
                        ]
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
                "trigger": "bling_pedido",
                "bling_order_id": _int(raw_order.get("id")),
                "product_id": str(product.id),
                "link_ids": link_ids,
                "sku": sku,
            },
        )
        session.add(job)
        await session.flush()
        jobs.append((job.id, product.id, link_ids))
    return jobs, notif


async def _store_label(session: AsyncSession, bling_loja_id: int | None) -> str | None:
    if bling_loja_id is None:
        return None
    row = (
        await session.execute(
            select(Store).where(Store.bling_store_id == bling_loja_id).limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return f"loja {bling_loja_id}"
    return row.apelido_override or row.marketplace.value


async def _notify_sale_telegram(
    session: AsyncSession,
    *,
    user_id: UUID,
    raw_order: dict[str, Any],
    notif_items: list[dict[str, Any]],
) -> None:
    us = await session.get(UserSettings, user_id)
    if us is None or not us.notify_telegram:
        return
    loja = raw_order.get("loja") or {}
    bling_loja_id = _int(loja.get("id")) if isinstance(loja, dict) else None
    store_label = await _store_label(session, bling_loja_id)
    numero = raw_order.get("numero")
    total = _num(raw_order.get("total"))
    lines = [f"<b>Venda Bling</b> — pedido <code>{numero}</code>"]
    if store_label:
        lines.append(f"Loja: {store_label}")
    if total is not None:
        lines.append(f"Total: R$ {total:.2f}")
    for it in notif_items:
        mark = "" if it["matched"] else " ⚠ não importado"
        desc = (it.get("desc") or "")[:60]
        sku = it.get("sku") or "—"
        qty = it.get("qty")
        qty_s = f"×{qty}" if qty is not None else ""
        lines.append(f"• {sku} {qty_s} — {desc}{mark}")
    from app.services.telegram import TelegramClient

    await TelegramClient().safe_send(
        "\n".join(lines), chat_id=us.telegram_chat_id
    )


async def run_ingest_bling_order(
    session: AsyncSession,
    *,
    bling_order_id: int,
    user_id: UUID,
    event: str | None,
) -> dict[str, Any]:
    """Worker entrypoint. Fetches order from Bling and upserts (or marks excluded).
    For non-exclusion events, also enqueues a SYNC_PRODUCT job per matched SKU
    so the sale-driven stock refresh is visible in the jobs UI, and optionally
    sends a Telegram notification."""
    if event in ("pedido.exclusao", "order.deleted"):
        n = await mark_order_excluido(session, bling_order_id)
        await session.commit()
        logger.info(
            "bling_order_excluido", bling_order_id=bling_order_id, rows=n
        )
        return {"ok": True, "rows": n, "deleted": True}

    client = await _bling_client_for_user(session, user_id)
    if client is None:
        logger.warning(
            "bling_order_ingest_no_integration",
            bling_order_id=bling_order_id,
            user_id=str(user_id),
        )
        return {"ok": False, "error": "no_bling_integration"}

    raw = await client.get_order(bling_order_id)
    if not raw:
        logger.warning(
            "bling_order_ingest_empty", bling_order_id=bling_order_id
        )
        return {"ok": False, "error": "empty_order"}

    n = await upsert_order(session, raw)
    jobs, notif_items = await _enqueue_stock_refresh_for_order(
        session, raw_order=raw, user_id=user_id
    )
    await session.commit()

    if jobs:
        pool = await get_arq_pool()
        for job_id, product_id, link_ids in jobs:
            arq = await pool.enqueue_job(
                "sync_product_run",
                str(job_id),
                str(user_id),
                str(product_id),
                link_ids or None,
            )
            if arq is not None:
                job = await session.get(BackgroundJob, job_id)
                if job is not None:
                    job.arq_job_id = arq.job_id
        await session.commit()

    try:
        await _notify_sale_telegram(
            session, user_id=user_id, raw_order=raw, notif_items=notif_items
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("bling_order_telegram_failed", err=str(e))

    logger.info(
        "bling_order_ingested",
        bling_order_id=bling_order_id,
        bling_event=event,
        rows=n,
        refresh_jobs=len(jobs),
    )
    return {
        "ok": True,
        "rows": n,
        "deleted": False,
        "refresh_jobs": len(jobs),
    }
