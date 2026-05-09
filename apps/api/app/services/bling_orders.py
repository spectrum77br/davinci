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
    BlingOrder,
    Integration,
    IntegrationPlatform,
    Store,
)
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketplaces.bling import BlingClient

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


async def run_ingest_bling_order(
    session: AsyncSession,
    *,
    bling_order_id: int,
    user_id: UUID,
    event: str | None,
) -> dict[str, Any]:
    """Worker entrypoint. Fetches order from Bling and upserts (or marks excluded)."""
    if event == "pedido.exclusao":
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
    await session.commit()
    logger.info(
        "bling_order_ingested",
        bling_order_id=bling_order_id,
        event=event,
        rows=n,
    )
    return {"ok": True, "rows": n, "deleted": False}
