"""Fase 2 da aba Kit: criar produto composto (formato='E') no Bling
quando o operador marca "x" na matriz.

Triggered por ARQ via `create_bling_kit_for_mark` (registrado em
worker.py). Idempotente — pula se mark.bling_product_id já preenchido.

Fluxo:
  1. Carrega mark + base + variation
  2. Parse variation.code → componentes (tamanhos + acessórios)
  3. Resolve bling_product_id de cada componente em `products`
  4. Get/create categoria "mala kit" no Bling
  5. Gera nome via generate_kit_name
  6. POST /produtos no Bling com formato="E" + estrutura
  7. Atualiza mark.bling_product_id + status='sent' (ou 'error')

Erros não fazem retry automático — operador clica resync na UI.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import (
    ImportKitBase,
    ImportKitMark,
    ImportKitVariation,
    Integration,
    IntegrationPlatform,
    Product,
)
from app.security.cipher import decrypt_json, encrypt_json
from app.services.importacao_naming import generate_kit_name, parse_kit_variation
from app.services.marketplaces.bling import BlingClient

logger = structlog.get_logger()

# Nome da categoria Bling onde os kits criados vão parar. Operador
# definiu isso. Pode ser ajustado no Bling se necessário — o helper
# `find_or_create_category` cria automaticamente se não existir.
_KIT_CATEGORY_NAME = "mala kit"

# Defaults pra estrutura do composto no Bling:
#   * tipoEstoque="V" (virtual): estoque do kit é derivado dos componentes
#   * lancamentoEstoque="M" (componente): venda do kit deduz só dos componentes
# Operador validou que a operação trabalha kit+componente como estoques
# independentes lógicos — quando vende o kit, sai só do estoque do kit,
# mas como o estoque do kit é virtual, o Bling deduz dos componentes
# automaticamente.
_ESTRUTURA_TIPO_ESTOQUE = "V"
_ESTRUTURA_LANCAMENTO = "M"


async def _bling_client(session: AsyncSession) -> tuple[BlingClient | None, Integration | None]:
    integ = (await session.execute(
        select(Integration)
        .where(Integration.platform == IntegrationPlatform.BLING)
        .limit(1)
    )).scalar_one_or_none()
    if integ is None:
        return None, None
    creds = decrypt_json(integ.credentials)

    async def _persist(new_creds: dict) -> None:
        integ.credentials = encrypt_json(new_creds)
        exp = new_creds.get("expires_at")
        if exp:
            integ.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        await session.flush()

    return BlingClient(creds, on_token_refresh=_persist, integration_id=integ.id), integ


def _resolve_component_skus(sku_base: str, variation_code: str) -> list[str]:
    """variation_code → lista de SKUs de componente em ordem.
    Tamanhos numéricos viram `{sku_base}.{N}`; acessórios vão como-são.
    """
    sizes, accessories = parse_kit_variation(variation_code)
    skus: list[str] = []
    for size in sizes:
        skus.append(f"{sku_base}.{size}")
    skus.extend(accessories)
    return skus


async def _resolve_component_bling_ids(
    session: AsyncSession, skus: list[str],
) -> tuple[list[tuple[str, int]], list[str]]:
    """Retorna ([(sku, bling_id)...], [skus_missing_bling_id])."""
    if not skus:
        return [], []
    rows = (await session.execute(
        select(Product.sku, Product.bling_product_id)
        .where(Product.sku.in_(skus))
    )).all()
    id_by_sku: dict[str, int | None] = {r.sku.lower(): r.bling_product_id for r in rows}
    resolved: list[tuple[str, int]] = []
    missing: list[str] = []
    for sku in skus:
        bid = id_by_sku.get(sku.lower())
        if bid is None:
            missing.append(sku)
        else:
            resolved.append((sku, int(bid)))
    return resolved, missing


async def _set_mark_error(
    session: AsyncSession, mark: ImportKitMark, error_msg: str,
) -> None:
    mark.bling_sync_status = "error"
    mark.bling_sync_error = error_msg[:1000]
    mark.bling_sync_attempted_at = datetime.now(UTC)
    await session.commit()


async def create_bling_kit_for_mark(mark_id: UUID | str) -> dict[str, Any]:
    """Cria o produto composto no Bling pra esse mark. Idempotente.

    Retorna `{"ok": bool, "bling_product_id": int|None, "error": str|None}`.
    """
    if isinstance(mark_id, str):
        mark_id = UUID(mark_id)

    async with session_scope() as session:
        mark = (await session.execute(
            select(ImportKitMark).where(ImportKitMark.id == mark_id)
        )).scalar_one_or_none()
        if mark is None:
            logger.warning("kit_sync_mark_missing", mark_id=str(mark_id))
            return {"ok": False, "error": "mark_not_found"}

        # Idempotência: já sincronizado.
        if mark.bling_product_id is not None:
            logger.info(
                "kit_sync_already_done",
                mark_id=str(mark_id),
                bling_product_id=mark.bling_product_id,
            )
            return {"ok": True, "bling_product_id": mark.bling_product_id, "skipped": True}

        base = (await session.execute(
            select(ImportKitBase).where(ImportKitBase.id == mark.base_id)
        )).scalar_one_or_none()
        variation = (await session.execute(
            select(ImportKitVariation).where(ImportKitVariation.id == mark.variation_id)
        )).scalar_one_or_none()
        if base is None or variation is None:
            await _set_mark_error(session, mark, "base_or_variation_not_found")
            return {"ok": False, "error": "base_or_variation_not_found"}

        # Resolver componentes.
        comp_skus = _resolve_component_skus(base.sku_base, variation.code)
        if not comp_skus:
            await _set_mark_error(session, mark, f"no_components_parsed_from={variation.code!r}")
            return {"ok": False, "error": "no_components_parsed"}

        resolved, missing = await _resolve_component_bling_ids(session, comp_skus)
        if missing:
            msg = f"missing_component_bling_id: {','.join(missing)}"
            await _set_mark_error(session, mark, msg)
            return {"ok": False, "error": msg}

        # Bling client.
        client, integ = await _bling_client(session)
        if client is None or integ is None:
            await _set_mark_error(session, mark, "no_bling_integration")
            return {"ok": False, "error": "no_bling_integration"}

        # Categoria.
        try:
            category_id = await client.find_or_create_category(_KIT_CATEGORY_NAME)
        except Exception as e:  # noqa: BLE001
            await _set_mark_error(session, mark, f"category_resolve_failed: {e}")
            logger.warning("kit_sync_category_failed", mark_id=str(mark_id), err=str(e)[:200])
            return {"ok": False, "error": f"category_resolve_failed: {e}"}

        # SKU + nome do kit.
        kit_sku = f"{base.sku_base}.{variation.code}"
        kit_name = generate_kit_name(
            base.modelo_bling, base.sku_base, variation.code, base.cor,
        )

        # Estrutura.
        estrutura = {
            "tipoEstoque": _ESTRUTURA_TIPO_ESTOQUE,
            "lancamentoEstoque": _ESTRUTURA_LANCAMENTO,
            "componentes": [
                {"produto": {"id": bling_id}, "quantidade": 1}
                for _, bling_id in resolved
            ],
        }

        # Criar no Bling.
        try:
            data = await client.create_product(
                sku=kit_sku,
                name=kit_name,
                category_id=category_id,
                formato="E",
                estrutura=estrutura,
            )
        except httpx.HTTPStatusError as e:
            body_excerpt = (e.response.text or "")[:500]
            await _set_mark_error(
                session, mark,
                f"bling_http_{e.response.status_code}: {body_excerpt}",
            )
            logger.warning(
                "kit_sync_bling_http",
                mark_id=str(mark_id), sku=kit_sku,
                status=e.response.status_code, body=body_excerpt,
            )
            return {"ok": False, "error": "bling_http_error"}
        except Exception as e:  # noqa: BLE001
            await _set_mark_error(session, mark, f"bling_call_failed: {e}")
            logger.exception("kit_sync_bling_call_failed", mark_id=str(mark_id))
            return {"ok": False, "error": f"bling_call_failed: {e}"}

        new_bling_id = data.get("id")
        if new_bling_id is None:
            await _set_mark_error(session, mark, f"bling_no_id_returned: {data}")
            return {"ok": False, "error": "bling_no_id_returned"}

        # Sucesso — gravar.
        now = datetime.now(UTC)
        mark.bling_product_id = int(new_bling_id)
        mark.bling_sync_status = "sent"
        mark.bling_sync_error = None
        mark.bling_sync_attempted_at = now
        mark.bling_sync_done_at = now
        await session.commit()
        logger.info(
            "kit_sync_done",
            mark_id=str(mark_id),
            bling_product_id=int(new_bling_id),
            sku=kit_sku,
            components=len(resolved),
        )
        return {"ok": True, "bling_product_id": int(new_bling_id)}


async def create_bling_kit_for_mark_job(ctx: dict, mark_id: str) -> dict[str, Any]:
    """ARQ wrapper. Recebe mark_id como string (JSON-safe)."""
    return await create_bling_kit_for_mark(mark_id)
