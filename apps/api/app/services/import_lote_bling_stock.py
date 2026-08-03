"""Lança entradas de estoque no Bling para cada item de um lote fechado.

Disparo: PATCH /lotes/{id} setando `fechamento` (NULL → date) numa
categoria 'celular' ou 'mala'. O router enfileira `push_lote_stock_to_bling_job`
na fila UI; o worker resolve o `bling_product_id` do ImportProduct de
cada ImportLoteItem e chama `POST /Api/v3/estoques` com operação 'E'
(Entrada — soma à quantidade atual).

Idempotente:
- Pula items com `bling_stock_pushed_at` preenchido (já enviados).
- Commit por item: cada entrada lançada é marcada no banco na hora,
  então timeout/crash no meio do lote nunca perde marcação (re-rodar
  o job continua de onde parou, sem duplicar).
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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import (
    ImportLote,
    ImportLoteItem,
    ImportProduct,
    Integration,
    IntegrationPlatform,
    Product,
)
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketplaces.bling import BlingClient

logger = structlog.get_logger()

# Celular + mala usam esse fluxo (mala habilitada 2026-08-03 a pedido do
# operador: fechar o lote deve lançar as quantidades no Bling). Eletro
# segue fora — fecha lote sem tocar no Bling.
_TARGET_CATEGORIAS = frozenset({"celular", "mala"})


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


async def _resolve_bling_product_id(
    session: AsyncSession, client: BlingClient, sku: str,
) -> int | None:
    """Resolve o bling_product_id pelo SKU. Estratégia em cascata:
    1) tabela local `products` (rápido, sem rede)
    2) busca por código no Bling V3 (find_active_product_by_sku)
    Retorna None se nenhum dos dois acha — caller decide skip."""
    if not sku:
        return None
    pid = (await session.execute(
        select(Product.bling_product_id)
        .where(
            func.lower(func.btrim(Product.sku)) == sku.strip().lower(),
            Product.bling_product_id.is_not(None),
        )
        .order_by((Product.situacao == "A").desc().nullslast())
        .limit(1)
    )).scalar_one_or_none()
    if pid:
        return int(pid)
    try:
        product = await client.find_active_product_by_sku(sku)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "import_lote_stock_find_sku_failed", sku=sku, error=str(exc)[:200],
        )
        return None
    return int(product["id"]) if product else None


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
        if lote.categoria not in _TARGET_CATEGORIAS:
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
                await session.commit()
                continue
            # SKU destino: override do item (operador escolheu redirecionar
            # via dropdown na aba Celular) tem prioridade sobre o SKU do
            # ImportProduct. Útil quando o lote tem i203.sa mas o operador
            # quer a entrada em i203.sp (mesmo modelo, tag diferente).
            target_sku = (item.bling_stock_target_sku or product.sku or "").strip()

            # Resolve o bling_product_id. Se NÃO houver override, tenta o
            # `product.bling_product_id` salvo (rápido). Em qualquer outro
            # caso, resolve pelo SKU (local + Bling). Só persiste de volta
            # em ImportProduct quando o target_sku é o SKU dele mesmo.
            bling_pid: int | None = None
            same_sku = target_sku.lower() == (product.sku or "").strip().lower()
            if same_sku and product.bling_product_id:
                bling_pid = int(product.bling_product_id)
            if bling_pid is None:
                bling_pid = await _resolve_bling_product_id(
                    session, client, target_sku,
                )
                if bling_pid is not None and same_sku and not product.bling_product_id:
                    product.bling_product_id = bling_pid
            if bling_pid is None:
                item.bling_stock_status = "skipped"
                item.bling_stock_error = "sku_nao_encontrado_no_bling"
                summary["skipped"] += 1
                logger.warning(
                    "import_lote_stock_item_no_bling_product",
                    lote_id=str(lote_id), item_id=str(item.id),
                    product_id=str(product.id),
                    sku=product.sku, target_sku=target_sku,
                )
                await session.commit()
                continue

            try:
                await client.update_stock_by_id(
                    bling_pid,
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
                    sku=product.sku, target_sku=target_sku,
                    qty=int(item.quantidade),
                    bling_product_id=bling_pid,
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

            # Commit POR ITEM: o POST no Bling já aconteceu — persistir a
            # marcação na hora garante que timeout/crash no meio do lote
            # não desfaça o 'sent' (incidente ML27/ML28 3/ago: rollback
            # deixou 42 items lançados no Bling sem marca no banco, com
            # risco de double-post no retry).
            await session.commit()

        await session.commit()

    logger.info("import_lote_stock_done", lote_id=str(lote_id), **summary)
    return summary


async def push_lote_stock_to_bling_job(
    ctx: dict, lote_id: str,
) -> dict[str, Any]:
    """ARQ wrapper. Recebe lote_id como string (JSON-safe)."""
    return await push_lote_stock_to_bling(lote_id)
