"""Lança entradas de estoque no Bling para cada item de um lote fechado.

Disparo: PATCH /lotes/{id} setando `fechamento` (NULL → date) numa
categoria 'celular'. O router enfileira `push_lote_stock_to_bling_job`
na fila UI; o worker resolve o `bling_product_id` do ImportProduct de
cada ImportLoteItem e chama `POST /Api/v3/estoques` com operação 'E'
(Entrada — soma à quantidade atual).

Idempotente:
- Pula items com `bling_stock_pushed_at` preenchido (já enviados).
- Item sem `bling_product_id` no ImportProduct: registra status
  'skipped' com a razão; segue os outros. Operador resolve enviando
  o produto pro Bling primeiro e re-fechando o lote.

Observação no Bling: `observacoes = "Lote {nome} — entrada"`. Sem
custo (operador pediu).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import (
    ImportLote,
    ImportLoteItem,
    ImportProduct,
    Integration,
    IntegrationPlatform,
)
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketplaces.bling import BlingClient

logger = structlog.get_logger()

# Só celular usa esse fluxo por enquanto. Mala/Eletro fecham lote sem
# tocar no Bling (compra é em moeda diferente, sem entrada automática).
_TARGET_CATEGORIA = "celular"


async def _bling_client(
    session: AsyncSession,
) -> tuple[BlingClient | None, Integration | None]:
    integ = (
        await session.execute(
            select(Integration)
            .where(Integration.platform == IntegrationPlatform.BLING)
            .limit(1)
        )
    ).scalar_one_or_none()
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


async def push_lote_stock_to_bling(lote_id: UUID | str) -> dict[str, Any]:
    """Lança entradas no Bling pra cada item do lote. Best-effort por
    item (uma falha não bloqueia as outras). Retorna resumo agregado:
    `{ok: bool, total: int, sent: int, skipped: int, errors: int}`."""
    if isinstance(lote_id, str):
        lote_id = UUID(lote_id)

    summary = {"ok": True, "total": 0, "sent": 0, "skipped": 0, "errors": 0}

    async with session_scope() as session:
        lote = (await session.execute(
            select(ImportLote).where(ImportLote.id == lote_id)
        )).scalar_one_or_none()
        if lote is None:
            logger.warning("import_lote_stock_missing", lote_id=str(lote_id))
            return {"ok": False, "error": "lote_not_found"}
        if lote.categoria != _TARGET_CATEGORIA:
            logger.info(
                "import_lote_stock_skip_categoria",
                lote_id=str(lote_id), categoria=lote.categoria,
            )
            return {"ok": True, "skipped_categoria": lote.categoria}
        if lote.fechamento is None:
            logger.info("import_lote_stock_skip_aberto", lote_id=str(lote_id))
            return {"ok": True, "skipped_aberto": True}

        items = (await session.execute(
            select(ImportLoteItem, ImportProduct)
            .join(ImportProduct, ImportProduct.id == ImportLoteItem.product_id)
            .where(ImportLoteItem.lote_id == lote_id)
        )).all()
        summary["total"] = len(items)
        if not items:
            return summary

        client, _integ = await _bling_client(session)
        if client is None:
            logger.warning("import_lote_stock_no_integration", lote_id=str(lote_id))
            return {"ok": False, "error": "no_bling_integration"}

        obs = f"Lote {lote.nome} — entrada"

        for item, product in items:
            if item.bling_stock_pushed_at is not None:
                summary["skipped"] += 1
                continue
            if not item.quantidade or item.quantidade <= 0:
                item.bling_stock_status = "skipped"
                item.bling_stock_error = "quantidade_zero"
                summary["skipped"] += 1
                continue
            if not product.bling_product_id:
                item.bling_stock_status = "skipped"
                item.bling_stock_error = "produto_sem_bling_product_id"
                summary["skipped"] += 1
                logger.warning(
                    "import_lote_stock_item_no_bling_product",
                    lote_id=str(lote_id), item_id=str(item.id),
                    product_id=str(product.id), sku=product.sku,
                )
                continue

            try:
                await client.update_stock_by_id(
                    int(product.bling_product_id),
                    qty=int(item.quantidade),
                    operation="E",
                    observacao=obs,
                )
                item.bling_stock_status = "sent"
                item.bling_stock_error = None
                item.bling_stock_pushed_at = datetime.now(UTC)
                summary["sent"] += 1
                logger.info(
                    "import_lote_stock_item_sent",
                    lote_id=str(lote_id), item_id=str(item.id),
                    sku=product.sku, qty=int(item.quantidade),
                    bling_product_id=int(product.bling_product_id),
                )
            except Exception as exc:  # noqa: BLE001
                item.bling_stock_status = "error"
                item.bling_stock_error = str(exc)[:1000]
                summary["errors"] += 1
                summary["ok"] = False
                logger.error(
                    "import_lote_stock_item_error",
                    lote_id=str(lote_id), item_id=str(item.id),
                    sku=product.sku, error=str(exc)[:200],
                )

        await session.commit()

    logger.info("import_lote_stock_done", lote_id=str(lote_id), **summary)
    return summary


async def push_lote_stock_to_bling_job(
    ctx: dict, lote_id: str,
) -> dict[str, Any]:
    """ARQ wrapper. Recebe lote_id como string (JSON-safe)."""
    return await push_lote_stock_to_bling(lote_id)
