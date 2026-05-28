"""Return a devolved product to Bling stock.

Rules (per condition):
  Novo     → find product by SKU in Bling, add 1 via Entrada.
  Usado    → find product by SKU[:-3]+'.us'; if not found, create a new product
             under the next available z000X code and add 1.
  Trocado  → o pedido foi enviado errado; o item que de fato voltou é o
             `troca_sku` escolhido no modal, lançado como `troca_condicao`
             (Novo/Usado). O `row.sku` (produto vendido no Bling) é ignorado.
  Others   → no-op.

Modal de sufixo (`estoque_suffix`):
  Quando o SKU que vai voltar termina em `.sp`, o operador escolhe outro
  sufixo regional (`.ra`, `.pi`, …). O destino passa a ser `base.<suffix>` e
  a entrada é feita direto nesse SKU (criando o produto se não existir). Essa
  escolha SOBREPÕE a transformação `.us` do fluxo Usado — o operador
  redirecionou fisicamente a unidade para aquele bin.

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
# Condições que disparam retorno de estoque a partir do router. "Trocado"
# entra aqui porque a entrada é definida pelo modal (troca_sku/troca_condicao).
_STOCK_TRIGGER_CONDICOES = _STOCK_CONDICOES | {"Trocado"}

# Sufixos regionais válidos (espelha _SUFFIX_TAGS em routers/estoque.py).
# Usado para identificar/strip da base do SKU ao redirecionar via modal.
_SUFFIX_TAGS = ("ci", "pi", "ra", "sa", "sp", "us", "cd")

type StockResult = dict[str, Any]


def _sku_base(sku: str) -> str:
    """Remove o sufixo regional conhecido do SKU, retornando a base.
    `x001.sp` → `x001`; `x001` → `x001`."""
    if "." in sku:
        head, tail = sku.rsplit(".", 1)
        if tail.lower() in _SUFFIX_TAGS:
            return head
    return sku


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
    *,
    troca_sku: str | None = None,
    troca_condicao: str | None = None,
    estoque_suffix: str | None = None,
) -> StockResult | None:
    """Best-effort stock return. Returns a result dict; never raises to the caller.

    `troca_sku`/`troca_condicao`: usados quando condicao == "Trocado" — o item
    que volta é `troca_sku`, lançado como `troca_condicao` (Novo/Usado).
    `estoque_suffix`: redireciona a entrada para `base.<suffix>` (modal `.sp`).
    """
    if condicao not in _STOCK_TRIGGER_CONDICOES:
        return None

    # Resolve o SKU e a condição efetivos (Trocado é dirigido pelo modal).
    if condicao == "Trocado":
        eff_sku = (troca_sku or "").strip()
        eff_condicao = (troca_condicao or "").strip()
        if not eff_sku:
            return _result(False, "no_troca_sku", message="Trocado sem SKU de retorno selecionado")
        if eff_condicao not in _STOCK_CONDICOES:
            return _result(False, "no_troca_condicao", sku=eff_sku, message="Trocado sem condição (Novo/Usado) selecionada")
    else:
        eff_sku = (row.sku or "").strip()
        eff_condicao = condicao

    ctx = {
        "devolution_id": str(row.id),
        "condicao": condicao,
        "eff_condicao": eff_condicao,
        "sku": row.sku,
        "eff_sku": eff_sku,
        "estoque_suffix": estoque_suffix,
    }

    client = await _get_bling_client(session)
    if client is None:
        logger.warning("devolution_stock_no_bling_integration", **ctx)
        return _result(False, "no_integration", message="Nenhuma integração Bling encontrada")

    try:
        # Modal de sufixo: redireciona a entrada para base.<suffix> (sobrepõe .us).
        if estoque_suffix and (suffix := estoque_suffix.strip().lower().lstrip(".")):
            return await _return_with_suffix(client, eff_sku, suffix, row, ctx)
        if eff_condicao == "Novo":
            return await _return_novo(client, eff_sku, ctx)
        return await _return_usado(client, eff_sku, row, ctx)
    except Exception as exc:  # noqa: BLE001
        logger.error("devolution_stock_return_error", error=str(exc), **ctx)
        return _result(False, "error", message=str(exc))


async def _return_novo(client: BlingClient, sku: str, ctx: dict) -> StockResult:
    if not sku:
        logger.warning("devolution_stock_novo_no_sku", **ctx)
        return _result(False, "no_sku", message="Produto sem SKU — estoque não atualizado")

    product = await client.find_active_product_by_sku(sku)
    if product is None:
        logger.warning("devolution_stock_novo_sku_not_found", **ctx)
        return _result(False, "sku_not_found", sku=sku, message=f"SKU {sku} não encontrado no Bling")

    pid = int(product["id"])
    await client.update_stock_by_id(pid, qty=1, operation="E")
    logger.info("devolution_stock_entry_novo", bling_product_id=pid, **ctx)
    return _result(True, "entry_novo", sku=sku, bling_product_id=pid, message=f"SKU {sku} · +1 unidade adicionada")


async def _return_with_suffix(
    client: BlingClient, eff_sku: str, suffix: str, row: Devolution, ctx: dict
) -> StockResult:
    """Entrada direta em base.<suffix>; cria o produto se não existir.
    Usado pelo modal de SKU `.sp` — o operador escolheu o bin regional."""
    if not eff_sku:
        logger.warning("devolution_stock_suffix_no_sku", **ctx)
        return _result(False, "no_sku", message="Produto sem SKU — estoque não atualizado")

    target_sku = f"{_sku_base(eff_sku)}.{suffix}"
    product = await client.find_active_product_by_sku(target_sku)
    if product is not None:
        pid = int(product["id"])
        await client.update_stock_by_id(pid, qty=1, operation="E")
        logger.info("devolution_stock_entry_suffix", target_sku=target_sku, bling_product_id=pid, **ctx)
        return _result(True, "entry_suffix", sku=target_sku, bling_product_id=pid, message=f"SKU {target_sku} · +1 unidade adicionada")

    # Não existe a variante — cria o produto com o sufixo escolhido.
    nome = row.produtos or f"{target_sku}"
    price = float(row.custo_produto) if row.custo_produto else None
    new_data = await client.create_product(sku=target_sku, name=nome, price=price)
    product_id = (new_data or {}).get("id")
    if not product_id:
        logger.warning("devolution_stock_suffix_create_no_id", target_sku=target_sku, **ctx)
        return _result(False, "create_failed", sku=target_sku, message=f"Falha ao criar produto {target_sku} no Bling")

    pid = int(product_id)
    await client.update_stock_by_id(pid, qty=1, operation="E")
    logger.info("devolution_stock_created_suffix", target_sku=target_sku, bling_product_id=pid, **ctx)
    return _result(True, "product_created_suffix", sku=target_sku, bling_product_id=pid, message=f"Produto {target_sku} criado no Bling · +1 unidade")


async def _return_usado(client: BlingClient, sku: str, row: Devolution, ctx: dict) -> StockResult:
    sku = sku or ""

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
    nome = row.produtos or (f"Usado - {sku}" if sku else "Produto Usado")
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
