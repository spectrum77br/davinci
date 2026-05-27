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
from sqlalchemy.orm import aliased

from app.db import session_scope
from app.models import (
    ImportKitBase,
    ImportKitMark,
    ImportKitVariation,
    Integration,
    IntegrationPlatform,
    PricingProduct,
    Product,
    Segment,
)
from app.security.cipher import decrypt_json, encrypt_json
from app.services.import_product_bling_create import resolve_default_supplier_id
from app.services.importacao_naming import (
    build_kit_pricing_sku,
    generate_kit_name,
    kit_pricing_name,
    kit_pricing_segment_slug,
    parse_kit_variation,
)
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
) -> tuple[list[tuple[str, int, float]], list[str]]:
    """Retorna ([(sku, bling_id, cost)...], [skus_missing_bling_id]).
    `cost` é 0.0 quando o componente não tem bling_cost_price setado —
    permite usar para `sum()` sem checagem de None."""
    if not skus:
        return [], []
    rows = (await session.execute(
        select(Product.sku, Product.bling_product_id, Product.bling_cost_price)
        .where(Product.sku.in_(skus))
    )).all()
    by_sku: dict[str, tuple[int | None, float]] = {
        r.sku.lower(): (r.bling_product_id, float(r.bling_cost_price or 0))
        for r in rows
    }
    resolved: list[tuple[str, int, float]] = []
    missing: list[str] = []
    for sku in skus:
        info = by_sku.get(sku.lower())
        if info is None or info[0] is None:
            missing.append(sku)
        else:
            resolved.append((sku, int(info[0]), info[1]))
    return resolved, missing


async def _set_mark_error(
    session: AsyncSession, mark: ImportKitMark, error_msg: str,
) -> None:
    mark.bling_sync_status = "error"
    mark.bling_sync_error = error_msg[:1000]
    mark.bling_sync_attempted_at = datetime.now(UTC)
    await session.commit()


async def _resolve_kit_segment(
    session: AsyncSession, variation_code: str,
) -> Segment:
    """Resolve o Segment correto pra esta variation. Sempre filho de
    'mala' — query usa join self-FK pra garantir.
    Erra se o segmento não existir no DB (operacional precisa criar)."""
    slug = kit_pricing_segment_slug(variation_code)
    parent = aliased(Segment)
    seg = (await session.execute(
        select(Segment)
        .join(parent, parent.id == Segment.parent_id)
        .where(Segment.slug == slug, parent.slug == "mala")
        .limit(1)
    )).scalar_one_or_none()
    if seg is None:
        raise ValueError(f"segment not found: parent=mala, slug={slug!r}")
    return seg


async def _ensure_pricing_product_for_kit(
    session: AsyncSession,
    mark: ImportKitMark,
    base: ImportKitBase,
    variation: ImportKitVariation,
    *,
    owner_user_id: UUID,
) -> PricingProduct:
    """Cria ou atualiza pricing_product correspondente ao kit.

    Unicidade lógica: (user_id, name, segment_id). Várias cores da
    mesma família compartilham uma row, com sku comma-separated.

    Campos não-preenchidos automaticamente (operador faz à mão):
      * cost_kit1..4
      * bling_cost_price
      * description, ean, model
      * dimensões / dados fiscais
      * department (NULL — agrupado só por segment_id em mala)
    """
    segment = await _resolve_kit_segment(session, variation.code)
    name = kit_pricing_name(base.modelo_bling, variation.code)
    new_sku_piece = build_kit_pricing_sku(base.sku_base, variation.code)

    # Lookup existente.
    existing = (await session.execute(
        select(PricingProduct).where(
            PricingProduct.user_id == owner_user_id,
            PricingProduct.name == name,
            PricingProduct.segment_id == segment.id,
        ).limit(1)
    )).scalar_one_or_none()

    if existing is None:
        pp = PricingProduct(
            user_id=owner_user_id,
            name=name,
            sku=new_sku_piece,
            segment_id=segment.id,
        )
        session.add(pp)
        await session.flush()
        logger.info(
            "kit_pricing_created",
            mark_id=str(mark.id), pricing_product_id=str(pp.id),
            name=name, sku=new_sku_piece, segment=segment.slug,
        )
        return pp

    # Já existe — adicionar new_sku_piece se ainda não está lá.
    pieces = [s.strip() for s in (existing.sku or "").split(",") if s.strip()]
    if new_sku_piece not in pieces:
        pieces.append(new_sku_piece)
        # Mantém ordem de inserção (estável + previsível pra operador).
        existing.sku = ",".join(pieces)
        await session.flush()
        logger.info(
            "kit_pricing_updated",
            mark_id=str(mark.id), pricing_product_id=str(existing.id),
            new_piece=new_sku_piece, total_pieces=len(pieces),
        )
    return existing


async def _set_pricing_error(
    session: AsyncSession, mark: ImportKitMark, error_msg: str,
) -> None:
    mark.pricing_sync_status = "error"
    mark.pricing_sync_error = error_msg[:1000]
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
        # SKU canônico — '.' entre tamanhos, '+' apenas pra acessórios.
        # Reusa o mesmo helper que constrói o SKU do pricing_product
        # (fase 3) pra garantir consistência: o que aparece no Bling
        # bate com o que aparece em /pricing/tabela. Variation
        # "8+18" → "base.8.18"; "12+20+24+a075..." → "base.12.20.24+a075...".
        kit_sku = build_kit_pricing_sku(base.sku_base, variation.code)
        kit_name = generate_kit_name(
            base.modelo_bling, base.sku_base, variation.code, base.cor,
        )

        # Estrutura.
        estrutura = {
            "tipoEstoque": _ESTRUTURA_TIPO_ESTOQUE,
            "lancamentoEstoque": _ESTRUTURA_LANCAMENTO,
            "componentes": [
                {"produto": {"id": bling_id}, "quantidade": 1}
                for _, bling_id, _cost in resolved
            ],
        }

        # Custo do kit = soma dos custos dos componentes (decisão
        # operacional). Cada componente já trouxe bling_cost_price em
        # `resolved`. Quando todos os componentes estão sem custo a
        # soma fica 0 → BlingClient omite precoCusto do payload.
        components_cost = sum(cost for _, _, cost in resolved)

        # Fornecedor padrão — anchor obrigatório pra precoCusto persistir.
        supplier_id = await resolve_default_supplier_id(client)
        if components_cost > 0 and supplier_id is None:
            logger.warning(
                "kit_sync_no_supplier",
                mark_id=str(mark_id), sku=kit_sku,
                components_cost=components_cost,
            )

        # Criar no Bling.
        try:
            data = await client.create_product(
                sku=kit_sku,
                name=kit_name,
                cost_price=components_cost if components_cost > 0 else None,
                supplier_id=supplier_id,
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

        # ── Fase 3: criar/atualizar pricing_product ─────────────────
        # Falha aqui NÃO desfaz o Bling — só registra o erro na mark.
        # Operador re-tenta via UI (POST /resync-pricing).
        if mark.pricing_product_id is None:
            try:
                pp = await _ensure_pricing_product_for_kit(
                    session, mark, base, variation,
                    owner_user_id=integ.user_id,
                )
                mark.pricing_product_id = pp.id
                mark.pricing_sync_status = "sent"
                mark.pricing_sync_error = None
                mark.pricing_sync_done_at = datetime.now(UTC)
                await session.commit()
            except Exception as e:  # noqa: BLE001
                await _set_pricing_error(session, mark, f"pricing_sync_failed: {e}")
                logger.exception(
                    "kit_pricing_sync_failed",
                    mark_id=str(mark_id),
                    bling_product_id=int(new_bling_id),
                )
                return {
                    "ok": True,
                    "bling_product_id": int(new_bling_id),
                    "pricing_error": str(e)[:200],
                }

        return {"ok": True, "bling_product_id": int(new_bling_id)}


async def create_bling_kit_for_mark_job(ctx: dict, mark_id: str) -> dict[str, Any]:
    """ARQ wrapper. Recebe mark_id como string (JSON-safe)."""
    return await create_bling_kit_for_mark(mark_id)


async def retry_pricing_sync_for_mark(mark_id: UUID | str) -> dict[str, Any]:
    """Re-tenta SÓ a parte de pricing_product, assumindo que o Bling
    create já foi bem-sucedido. Usado pelo endpoint POST
    /api/importacao/kit/mark/{id}/resync-pricing — operador pode
    re-tentar a sync de pricing sem refazer o Bling."""
    if isinstance(mark_id, str):
        mark_id = UUID(mark_id)

    async with session_scope() as session:
        mark = (await session.execute(
            select(ImportKitMark).where(ImportKitMark.id == mark_id)
        )).scalar_one_or_none()
        if mark is None:
            return {"ok": False, "error": "mark_not_found"}
        if mark.bling_product_id is None:
            return {"ok": False, "error": "bling_not_synced_yet"}
        if mark.pricing_product_id is not None:
            return {
                "ok": True,
                "pricing_product_id": str(mark.pricing_product_id),
                "skipped": True,
            }

        base = (await session.execute(
            select(ImportKitBase).where(ImportKitBase.id == mark.base_id)
        )).scalar_one_or_none()
        variation = (await session.execute(
            select(ImportKitVariation).where(ImportKitVariation.id == mark.variation_id)
        )).scalar_one_or_none()
        if base is None or variation is None:
            return {"ok": False, "error": "base_or_variation_not_found"}

        # Recupera owner_user_id da Integration (mesma fonte da fase 2).
        integ = (await session.execute(
            select(Integration)
            .where(Integration.platform == IntegrationPlatform.BLING)
            .limit(1)
        )).scalar_one_or_none()
        if integ is None:
            await _set_pricing_error(session, mark, "no_bling_integration_for_user_lookup")
            return {"ok": False, "error": "no_bling_integration"}

        try:
            pp = await _ensure_pricing_product_for_kit(
                session, mark, base, variation,
                owner_user_id=integ.user_id,
            )
            mark.pricing_product_id = pp.id
            mark.pricing_sync_status = "sent"
            mark.pricing_sync_error = None
            mark.pricing_sync_done_at = datetime.now(UTC)
            await session.commit()
            return {"ok": True, "pricing_product_id": str(pp.id)}
        except Exception as e:  # noqa: BLE001
            await _set_pricing_error(session, mark, f"pricing_sync_failed: {e}")
            logger.exception("kit_pricing_resync_failed", mark_id=str(mark_id))
            return {"ok": False, "error": str(e)[:200]}
