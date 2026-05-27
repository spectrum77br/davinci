"""Cria produto simples (formato='S') no Bling pra uma row de
import_products marcada como 'pending'.

Pattern espelha bling_kit_create.py (fase 2 do Kit), mas pra mala
individual: o usuário registra o produto na aba Mala da importação
com SKU+modelo+cor+custo, clica "Enviar pro Bling", a row vira
status='pending' e o worker ARQ cria o produto + grava localmente
em `products` linkado pelo bling_product_id (importante: kits da
aba Kit usam esses produtos como componentes).

Idempotente — pula se bling_product_id já preenchido.

Não há campo `tags` no shape de produto da Bling V3 (verificado via
SDK AlexandreBellas/bling-erp-api-js). A categoria "mala" via
`find_or_create_category` cobre o caso de uso de classificação.
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
    ImportProduct,
    Integration,
    IntegrationPlatform,
    Product,
)
from app.security.cipher import decrypt_json, encrypt_json
from app.services.importacao_naming import generate_mala_name
from app.services.marketplaces.bling import BlingClient

logger = structlog.get_logger()

# Categoria fixa pros produtos criados pela aba Mala. Mesma decisão
# operacional do kit ("mala kit") — diferenciar simples vs composto.
_MALA_CATEGORY_NAME = "mala"


async def _bling_client(
    session: AsyncSession,
) -> tuple[BlingClient | None, Integration | None]:
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


async def _set_error(
    session: AsyncSession, row: ImportProduct, error_msg: str,
) -> None:
    row.bling_sync_status = "error"
    row.bling_sync_error = error_msg[:1000]
    row.bling_sync_attempted_at = datetime.now(UTC)
    await session.commit()


async def _ensure_local_product(
    session: AsyncSession,
    *,
    row: ImportProduct,
    bling_product_id: int,
    user_id: UUID,
    integration_id: UUID,
    name: str,
) -> Product:
    """Cria ou atualiza Product local linkado ao bling_product_id.
    Necessário pra que kits da aba Kit possam referenciá-lo como
    componente. Idempotente: se já existe Product com este SKU,
    apenas atualiza bling_product_id."""
    existing = (await session.execute(
        select(Product).where(Product.sku == row.sku).limit(1)
    )).scalar_one_or_none()
    if existing is not None:
        # Atualiza link com Bling caso esteja vazio.
        if existing.bling_product_id is None:
            existing.bling_product_id = bling_product_id
        if existing.bling_cost_price is None and row.custo_bling is not None:
            existing.bling_cost_price = row.custo_bling
        if not existing.name:
            existing.name = name
        existing.situacao = existing.situacao or "A"
        existing.formato = existing.formato or "S"
        return existing

    product = Product(
        user_id=user_id,
        sku=row.sku,
        name=name,
        bling_product_id=bling_product_id,
        bling_cost_price=row.custo_bling,
        situacao="A",
        formato="S",
        integration_id=integration_id,
    )
    session.add(product)
    return product


async def sync_import_product_to_bling(import_product_id: UUID | str) -> dict[str, Any]:
    """Cria o produto simples no Bling pra um import_product. Idempotente.

    Retorna `{"ok": bool, "bling_product_id": int|None, "error": str|None}`.
    """
    if isinstance(import_product_id, str):
        import_product_id = UUID(import_product_id)

    async with session_scope() as session:
        row = (await session.execute(
            select(ImportProduct).where(ImportProduct.id == import_product_id)
        )).scalar_one_or_none()
        if row is None:
            logger.warning("import_product_sync_missing", id=str(import_product_id))
            return {"ok": False, "error": "import_product_not_found"}

        if row.bling_product_id is not None:
            logger.info(
                "import_product_sync_already_done",
                id=str(import_product_id),
                bling_product_id=row.bling_product_id,
            )
            return {"ok": True, "bling_product_id": row.bling_product_id, "skipped": True}

        if not row.sku:
            await _set_error(session, row, "sku_required")
            return {"ok": False, "error": "sku_required"}

        client, integ = await _bling_client(session)
        if client is None or integ is None:
            await _set_error(session, row, "no_bling_integration")
            return {"ok": False, "error": "no_bling_integration"}

        # Categoria.
        try:
            category_id = await client.find_or_create_category(_MALA_CATEGORY_NAME)
        except Exception as e:  # noqa: BLE001
            await _set_error(session, row, f"category_resolve_failed: {e}")
            logger.warning(
                "import_product_sync_category_failed",
                id=str(import_product_id), err=str(e)[:200],
            )
            return {"ok": False, "error": f"category_resolve_failed: {e}"}

        nome = generate_mala_name(row.modelo_bling, row.sku, row.cor)

        try:
            data = await client.create_product(
                sku=row.sku,
                name=nome,
                price=None,  # preço de VENDA continua manual no Bling
                cost_price=float(row.custo_bling) if row.custo_bling else None,
                category_id=category_id,
                formato="S",
            )
        except httpx.HTTPStatusError as e:
            body_excerpt = (e.response.text or "")[:500]
            await _set_error(
                session, row,
                f"bling_http_{e.response.status_code}: {body_excerpt}",
            )
            logger.warning(
                "import_product_sync_bling_http",
                id=str(import_product_id), sku=row.sku,
                status=e.response.status_code, body=body_excerpt,
            )
            return {"ok": False, "error": "bling_http_error"}
        except Exception as e:  # noqa: BLE001
            await _set_error(session, row, f"bling_call_failed: {e}")
            logger.exception(
                "import_product_sync_bling_failed",
                id=str(import_product_id),
            )
            return {"ok": False, "error": f"bling_call_failed: {e}"}

        new_bling_id = data.get("id")
        if new_bling_id is None:
            await _set_error(session, row, f"bling_no_id_returned: {data}")
            return {"ok": False, "error": "bling_no_id_returned"}

        new_bling_id = int(new_bling_id)

        # Cria Product local pra que kits possam usar como componente.
        await _ensure_local_product(
            session,
            row=row,
            bling_product_id=new_bling_id,
            user_id=integ.user_id,
            integration_id=integ.id,
            name=nome,
        )

        now = datetime.now(UTC)
        row.bling_product_id = new_bling_id
        row.bling_sync_status = "sent"
        row.bling_sync_error = None
        row.bling_sync_attempted_at = now
        row.bling_sync_done_at = now
        await session.commit()

        logger.info(
            "import_product_sync_done",
            id=str(import_product_id),
            bling_product_id=new_bling_id,
            sku=row.sku,
        )
        return {"ok": True, "bling_product_id": new_bling_id}


async def sync_import_product_to_bling_job(
    ctx: dict, import_product_id: str,
) -> dict[str, Any]:
    """ARQ wrapper. Recebe import_product_id como string (JSON-safe)."""
    return await sync_import_product_to_bling(import_product_id)
