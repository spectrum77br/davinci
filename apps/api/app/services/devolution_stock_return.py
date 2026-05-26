"""Return a devolved product to Bling stock.

Rules (per condition):
  Novo     → find product by SKU in Bling, add 1 via Entrada.
  Usado    → find product by SKU[:-3]+'.us'; if not found, create a new product
             under the next available z000X code and add 1.
  Others   → no-op.

All public functions return a result dict with keys:
  ok: bool, action: str, sku: str|None, bling_product_id: int|None, message: str
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from app.models import Integration
from app.models.enums import IntegrationPlatform
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketplaces.bling import BlingClient

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models import Devolution

logger = structlog.get_logger()

_STOCK_CONDICOES = {"Novo", "Usado"}

type StockResult = dict[str, Any]


def _result(
    ok: bool,
    action: str,
    *,
    sku: str | None = None,
    bling_product_id: int | None = None,
    message: str = "",
) -> StockResult:
    return {"ok": ok, "action": action, "sku": sku, "bling_product_id": bling_product_id, "message": message}


async def _get_bling_client(session: AsyncSession) -> BlingClient | None:
    from sqlalchemy import select

    integ = (
        await session.execute(
            select(Integration)
            .where(Integration.platform == IntegrationPlatform.BLING)
            .limit(1)
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

    return BlingClient(creds, on_token_refresh=_persist, integration_id=integ.id)


async def return_product_to_bling_stock(
    session: AsyncSession,
    row: Devolution,
    condicao: str,
) -> StockResult | None:
    """Best-effort stock return. Returns a result dict; never raises to the caller."""
    if condicao not in _STOCK_CONDICOES:
        return None

    ctx = {"devolution_id": str(row.id), "condicao": condicao, "sku": row.sku}

    client = await _get_bling_client(session)
    if client is None:
        logger.warning("devolution_stock_no_bling_integration", **ctx)
        return _result(False, "no_integration", message="Nenhuma integração Bling encontrada")

    try:
        if condicao == "Novo":
            return await _return_novo(client, row, ctx)
        return await _return_usado(client, row, ctx)
    except Exception as exc:  # noqa: BLE001
        logger.error("devolution_stock_return_error", error=str(exc), **ctx)
        return _result(False, "error", message=str(exc))


async def _return_novo(client: BlingClient, row: Devolution, ctx: dict) -> StockResult:
    if not row.sku:
        logger.warning("devolution_stock_novo_no_sku", **ctx)
        return _result(False, "no_sku", message="Produto sem SKU — estoque não atualizado")

    product = await client.find_active_product_by_sku(row.sku)
    if product is None:
        logger.warning("devolution_stock_novo_sku_not_found", **ctx)
        return _result(False, "sku_not_found", sku=row.sku, message=f"SKU {row.sku} não encontrado no Bling")

    pid = int(product["id"])
    await client.update_stock_by_id(pid, qty=1, operation="E")
    logger.info("devolution_stock_entry_novo", bling_product_id=pid, **ctx)
    return _result(True, "entry_novo", sku=row.sku, bling_product_id=pid, message=f"SKU {row.sku} · +1 unidade adicionada")


async def _return_usado(client: BlingClient, row: Devolution, ctx: dict) -> StockResult:
    sku = row.sku or ""

    # Compute the .us variant SKU.
    # If the SKU already has a dot-extension (e.g. x001.pi, x002.sa) replace it.
    # Otherwise strip the last 3 chars and append .us (e.g. ABC123 → ABC.us).
    if not sku:
        sku_usado = None
    elif "." in sku:
        sku_usado = sku.rsplit(".", 1)[0] + ".us"
    elif len(sku) > 3:
        sku_usado = sku[:-3] + ".us"
    else:
        sku_usado = sku + ".us"

    product = await client.find_active_product_by_sku(sku_usado) if sku_usado else None

    if product is not None:
        pid = int(product["id"])
        await client.update_stock_by_id(pid, qty=1, operation="E")
        logger.info("devolution_stock_entry_usado", sku_usado=sku_usado, bling_product_id=pid, **ctx)
        return _result(True, "entry_usado", sku=sku_usado, bling_product_id=pid, message=f"SKU {sku_usado} · +1 unidade adicionada")

    # Not found — create under the next available z-SKU
    z_sku = await client.find_next_z_sku()
    nome = row.produtos or (f"Usado - {row.sku}" if row.sku else "Produto Usado")
    price = float(row.custo_produto) if row.custo_produto else None
    category_id = await client.get_category_id_by_name("Usado")

    new_data = await client.create_product(sku=z_sku, name=nome, price=price, category_id=category_id)
    product_id = (new_data or {}).get("id")
    if not product_id:
        logger.warning("devolution_stock_create_product_no_id", z_sku=z_sku, **ctx)
        return _result(False, "create_failed", sku=z_sku, message=f"Falha ao criar produto {z_sku} no Bling")

    pid = int(product_id)
    await client.update_stock_by_id(pid, qty=1, operation="E")
    logger.info("devolution_stock_created_usado", z_sku=z_sku, bling_product_id=pid, original_sku=sku or None, **ctx)
    return _result(True, "product_created_usado", sku=z_sku, bling_product_id=pid, message=f"Produto {z_sku} criado no Bling · +1 unidade")
