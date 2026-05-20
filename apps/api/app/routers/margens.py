"""Margens — per-order margin rows with approval status."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import BlingOrder, Integration, IntegrationPlatform, Margens, User
from app.schemas.margens import (
    ALLOWED_STATUS,
    MargensMarketplaceOut,
    MargensMarketplacePage,
    MargensOut,
    MargensPatch,
)
from app.security.cipher import decrypt_json
from app.services.marketplaces.bling import BlingClient

logger = structlog.get_logger()
router = APIRouter(prefix="/api/margens", tags=["margens"])

SITUACAO_APROVADO = 6
SITUACAO_ATENDIDO = 9
SITUACAO_REPROVADO = 83955

# "Needs attention" flag — rows the user must triage. Three independent triggers:
#   1) margin below the configured minimum
#   2) seller paid more shipping than projected (negative frete result)
#   3) Bling-computed net diverges from marketplace net by more than 1%
# Rows that don't trigger any of these are treated as auto-approved in the UI
# (status filter "Pendente" hides them; "Aprovado" includes them).
# Frete Plataforma (per-item) — mirrors what the UI displays in that column.
# Used both in the SELECT (as `frete_plataforma`) and in the Frete Resultado
# computation, so the filter and the displayed values stay in sync.
# For Shopee, evento_freight can be negative (final_shipping_fee < 0 when the
# buyer pays for shipping — Shopee credits the seller). We floor at 0 because
# a credit is not a "frete plataforma" cost. NULL is preserved (= no financial
# synced yet), so the cell stays blank instead of showing 0.
_FRETE_PLATAFORMA_SQL = (
    "CASE "
    "WHEN COALESCE(v.plataforma_bling, v.plataforma_financeiro) = 'shopee' "
    "THEN CASE WHEN v.evento_freight IS NULL THEN NULL "
    "          ELSE GREATEST(v.evento_freight * v.item_proportion, 0::numeric) END "
    "ELSE v.marketplace_frete_real_cobrado_item "
    "END"
)
# Frete Resultado (per-item) = Frete Projetado − Frete Plataforma
_FRETE_RESULTADO_SQL = f"(v.frete_projetado_item - ({_FRETE_PLATAFORMA_SQL}))"

_ATTENTION_MARGEM_SQL = (
    "(v.marketplace_margem IS NOT NULL AND v.margem_minima IS NOT NULL "
    " AND v.marketplace_margem < v.margem_minima)"
)
_ATTENTION_FRETE_SQL = (
    f"(v.frete_projetado_item IS NOT NULL AND {_FRETE_RESULTADO_SQL} < 0)"
)
_ATTENTION_SALDO_SQL = (
    "(v.marketplace_liquido_base_margem_item IS NOT NULL "
    " AND v.bling_valorbase_item IS NOT NULL "
    " AND ABS("
    "       (v.bling_valorbase_item"
    "        - COALESCE(v.bling_custofrete_item, 0)"
    "        - COALESCE(v.bling_taxacomissao_item, 0))"
    "       - v.marketplace_liquido_base_margem_item"
    "    ) > 0.01 * ABS(v.marketplace_liquido_base_margem_item))"
)

# "Needs attention" flag — rows the user must triage. Three independent triggers:
#   1) margin below the configured minimum
#   2) seller paid more shipping than projected (negative frete result)
#   3) Bling-computed net diverges from marketplace net by more than 1%
# Rows that don't trigger any of these are treated as auto-approved in the UI
# (status filter "Pendente" hides them; "Aprovado" includes them).
NEEDS_ATTENTION_SQL = (
    f"({_ATTENTION_MARGEM_SQL} OR {_ATTENTION_FRETE_SQL} OR {_ATTENTION_SALDO_SQL})"
)

_ATTENTION_TYPE_MAP = {
    "margem": _ATTENTION_MARGEM_SQL,
    "frete":  _ATTENTION_FRETE_SQL,
    "saldo":  _ATTENTION_SALDO_SQL,
    "all":    NEEDS_ATTENTION_SQL,
}


@router.get("", response_model=list[MargensOut])
async def list_margens(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("margem", "view"))],
    status: str | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
) -> list[MargensOut]:
    stmt = select(Margens)
    if status:
        if status not in ALLOWED_STATUS:
            raise HTTPException(400, detail={"code": "invalid_status"})
        stmt = stmt.where(Margens.status == status)
    stmt = stmt.order_by(Margens.data.desc().nullslast(), Margens.created_at.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [MargensOut.model_validate(r) for r in rows]


@router.get("/marketplace", response_model=MargensMarketplacePage)
async def list_margens_marketplace(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("margem", "view"))],
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    search: str | None = Query(None),
    platform: str | None = Query(None),
    conta: str | None = Query(None),
    status: str | None = Query(None),
    attention_type: str | None = Query(None),
) -> MargensMarketplacePage:
    """Per-item marketplace conciliation rows (paginated, 20d window).

    Queries the live view (not the MV) so each page request returns
    fresh data. Filters apply server-side; pagination is offset/limit.
    `platforms` is the distinct list available — used by the UI dropdown.
    """
    where = ["TRUE"]
    params: dict = {"limit": limit, "offset": offset}
    if platform:
        where.append("COALESCE(v.plataforma_bling, v.plataforma_financeiro) = :platform")
        params["platform"] = platform
    if conta:
        where.append("v.loja_nome = :conta")
        params["conta"] = conta
    # attention_type narrows which "needs attention" trigger qualifies a row.
    # When the user picks a specific trigger (frete/margem/saldo), only rows
    # that hit that trigger are returned — regardless of the chosen status.
    # When attention_type='all' (default), status alone drives the filter.
    attention_sql = _ATTENTION_TYPE_MAP.get(attention_type or "all", NEEDS_ATTENTION_SQL)
    attention_active = attention_type and attention_type != "all"
    if attention_active:
        where.append(attention_sql)
    if status:
        # Effective status:
        #   Aprovado/Reprovado in DB → respected as-is
        #   NULL/Pendente in DB     → derived from NEEDS_ATTENTION_SQL
        #                               (true → Pendente, false → Aprovado)
        if status == "Pendente":
            where.append(
                f"(v.bling_status_margem IS NULL OR v.bling_status_margem = 'Pendente') "
                f"AND {NEEDS_ATTENTION_SQL}"
            )
        elif status == "Aprovado":
            where.append(
                f"(v.bling_status_margem = 'Aprovado' "
                f" OR ((v.bling_status_margem IS NULL OR v.bling_status_margem = 'Pendente') "
                f"     AND NOT {NEEDS_ATTENTION_SQL}))"
            )
        elif status == "Reprovado":
            where.append("v.bling_status_margem = 'Reprovado'")
    if search:
        where.append(
            "(v.pedido_bling ILIKE :q OR v.pedido_marketplace ILIKE :q "
            "OR v.loja_nome ILIKE :q OR v.sku ILIKE :q OR v.produto ILIKE :q "
            "OR v.pricing_account_name ILIKE :q)"
        )
        params["q"] = f"%{search}%"
    where_sql = " AND ".join(where)

    count_sql = text(
        f"SELECT count(*) FROM davinci.mv_conciliacao_margens_marketplace v WHERE {where_sql}"  # noqa: S608
    )
    platforms_sql = text(
        "SELECT DISTINCT COALESCE(plataforma_bling, plataforma_financeiro) AS p "
        "FROM davinci.mv_conciliacao_margens_marketplace "
        "WHERE COALESCE(plataforma_bling, plataforma_financeiro) IS NOT NULL "
        "ORDER BY 1"
    )
    contas_sql = text(
        "SELECT DISTINCT loja_nome "
        "FROM davinci.mv_conciliacao_margens_marketplace "
        "WHERE loja_nome IS NOT NULL "
        "ORDER BY 1"
    )
    items_sql = text(
        f"""
        SELECT
            v.bling_order_item_id,
            v.bling_id,
            v.data,
            v.pedido_bling,
            v.pedido_marketplace,
            COALESCE(v.plataforma_bling, v.plataforma_financeiro) AS plataforma,
            v.loja_nome                                          AS conta,
            v.sku,
            v.produto,
            v.quantidade,
            v.bling_custo_produtos                               AS custo_produto,
            {_FRETE_PLATAFORMA_SQL}                              AS frete_plataforma,
            (v.evento_frete_anuncio * v.item_proportion)         AS frete_anuncio,
            v.frete_projetado_item                               AS frete_projetado,
            CASE
                WHEN COALESCE(v.plataforma_bling, v.plataforma_financeiro) = 'shopee'
                THEN 0::numeric
                ELSE (v.marketplace_frete_item - v.marketplace_frete_real_cobrado_item)
            END                                                  AS reembolso,
            {_FRETE_RESULTADO_SQL}                               AS resultado_frete,
            v.marketplace_liquido_base_margem_item               AS saldo_plataforma,
            (v.bling_valorbase_item
                - COALESCE(v.bling_custofrete_item, 0)
                - COALESCE(v.bling_taxacomissao_item, 0))        AS saldo_bling,
            v.marketplace_liquido_base_margem_item               AS saldo_efetivo,
            v.marketplace_margem                                 AS margem,
            v.margem_minima,
            CASE
                WHEN v.bling_status_margem IN ('Aprovado', 'Reprovado')
                    THEN v.bling_status_margem
                WHEN {NEEDS_ATTENTION_SQL} THEN 'Pendente'
                ELSE 'Aprovado'
            END                                                  AS status,
            v.pricing_account_id,
            v.pricing_account_name,
            v.pricing_account_listing_type,
            v.pricing_leaf_segment_name,
            v.bling_listing_type,
            bo.observacao,
            {_ATTENTION_MARGEM_SQL}                              AS attention_margem,
            {_ATTENTION_FRETE_SQL}                               AS attention_frete,
            {_ATTENTION_SALDO_SQL}                               AS attention_saldo
        FROM davinci.mv_conciliacao_margens_marketplace v
        LEFT JOIN LATERAL (
            SELECT bo.observacao
            FROM davinci.bling_orders bo
            WHERE bo.numero = v.pedido_bling
              AND bo.observacao IS NOT NULL
            LIMIT 1
        ) bo ON TRUE
        WHERE {where_sql}
        ORDER BY v.data DESC NULLS LAST, v.pedido_bling DESC, v.bling_order_item_id
        LIMIT :limit OFFSET :offset
        """  # noqa: S608
    )

    total = (await session.execute(count_sql, params)).scalar_one()
    rows = (await session.execute(items_sql, params)).mappings().all()
    platforms = [r[0] for r in (await session.execute(platforms_sql)).all()]
    contas = [r[0] for r in (await session.execute(contas_sql)).all()]

    return MargensMarketplacePage(
        items=[MargensMarketplaceOut.model_validate(dict(r)) for r in rows],
        total=int(total or 0),
        limit=limit,
        offset=offset,
        platforms=platforms,
        contas=contas,
    )


class MarketplaceObsPatch(BaseModel):
    observacao: str | None = None


@router.patch("/marketplace/observacao/{pedido_bling}")
async def patch_marketplace_observacao(
    pedido_bling: str,
    body: MarketplaceObsPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("margem", "edit"))],
) -> dict:
    """Salva observacao em todas as linhas (itens) do pedido em bling_orders."""
    next_value = (body.observacao or "").strip() or None
    result = await session.execute(
        update(BlingOrder)
        .where(BlingOrder.numero == pedido_bling)
        .values(observacao=next_value)
    )
    await session.commit()
    if result.rowcount == 0:
        raise HTTPException(404, detail={"code": "bling_order_not_found"})
    logger.info(
        "marketplace_observacao_patched",
        pedido_bling=pedido_bling,
        rows=result.rowcount,
    )
    await _refresh_mv_silent(session)
    return {"pedido_bling": pedido_bling, "observacao": next_value, "rows": result.rowcount}


@router.post("/marketplace/{bling_order_item_id}/sync-from-marketplace")
async def sync_bling_from_marketplace(
    bling_order_item_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("margem", "edit"))],
) -> dict:
    """One-shot apply: copia bruto/taxas/frete do marketplace para bling_orders.

    Faz o Saldo Bling do item ficar igual ao Saldo Plataforma sem persistir
    nenhuma escolha — o próprio UPDATE é a ação. O `custofrete` usa o
    mesmo CASE da coluna Frete Plataforma (com GREATEST(.,0) para Shopee).
    """
    # Shopee: o escrow_amount ja embute commission rebates, vouchers, cashback
    # etc — entao gravamos o liquido direto em valor_base e zeramos
    # taxacomissao/custofrete pra que (valor_base − taxa − frete) = liquido.
    # Outras plataformas seguem o split por componentes (bruto / taxas / frete).
    row = (await session.execute(
        text(
            f"""
            SELECT
              CASE
                WHEN COALESCE(v.plataforma_bling, v.plataforma_financeiro) = 'shopee'
                THEN v.marketplace_liquido_base_margem_item
                ELSE v.marketplace_valor_bruto_item
              END AS valorbase,
              CASE
                WHEN COALESCE(v.plataforma_bling, v.plataforma_financeiro) = 'shopee'
                THEN 0::numeric
                ELSE v.marketplace_taxas_item
              END AS taxacomissao,
              CASE
                WHEN COALESCE(v.plataforma_bling, v.plataforma_financeiro) = 'shopee'
                THEN 0::numeric
                ELSE {_FRETE_PLATAFORMA_SQL}
              END AS custofrete,
              v.pedido_bling
            FROM davinci.vw_conciliacao_margens_marketplace v
            WHERE v.bling_order_item_id = :id
            LIMIT 1
            """  # noqa: S608
        ),
        {"id": str(bling_order_item_id)},
    )).first()
    if row is None:
        raise HTTPException(404, detail={"code": "row_not_found"})
    if row.valorbase is None and row.taxacomissao is None and row.custofrete is None:
        raise HTTPException(400, detail={"code": "no_marketplace_data"})

    result = await session.execute(
        update(BlingOrder)
        .where(BlingOrder.id == bling_order_item_id)
        .values(
            valorbase=row.valorbase,
            taxacomissao=row.taxacomissao,
            custofrete=row.custofrete,
        )
    )
    await session.commit()
    if result.rowcount == 0:
        raise HTTPException(404, detail={"code": "bling_order_not_found"})

    logger.info(
        "marketplace_synced_to_bling",
        bling_order_item_id=str(bling_order_item_id),
        pedido_bling=row.pedido_bling,
        valorbase=str(row.valorbase),
        taxacomissao=str(row.taxacomissao),
        custofrete=str(row.custofrete),
    )
    await _refresh_mv_silent(session)
    return {
        "ok": True,
        "valorbase": float(row.valorbase) if row.valorbase is not None else None,
        "taxacomissao": float(row.taxacomissao) if row.taxacomissao is not None else None,
        "custofrete": float(row.custofrete) if row.custofrete is not None else None,
    }


async def _refresh_mv_silent(session: AsyncSession) -> None:
    """Best-effort refresh of mv_conciliacao_margens_marketplace.

    Fires after PATCHes (status/observacao) so the user sees their
    change immediately on next page load. Swallows errors — refresh
    failure must never block the underlying mutation.
    """
    try:
        await session.execute(
            text(
                "REFRESH MATERIALIZED VIEW CONCURRENTLY "
                "davinci.mv_conciliacao_margens_marketplace"
            )
        )
        await session.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "mv_conciliacao_margens_marketplace_refresh_failed",
            error=str(e)[:200],
        )


@router.post("/marketplace/refresh")
async def refresh_marketplace_mv(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("margem", "view"))],
) -> dict:
    """Trigger MV refresh on-demand (UI 'atualizar' button)."""
    await session.execute(
        text(
            "REFRESH MATERIALIZED VIEW CONCURRENTLY "
            "davinci.mv_conciliacao_margens_marketplace"
        )
    )
    await session.commit()
    return {"refreshed": True}


class MarketplaceStatusPatch(BaseModel):
    status: str
    sku: str | None = None
    local_only: bool = False


@router.patch("/marketplace/status/{pedido_bling}")
async def patch_marketplace_status(
    pedido_bling: str,
    body: MarketplaceStatusPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("margem", "edit"))],
) -> dict:
    """Aprova/Reprova/Pendente pelo numero do pedido Bling — atualiza
    situacao via API Bling e bling_orders.status (todos os itens do pedido).
    Mirror do PATCH /api/margens/{id} mas operando por pedido.
    """
    new_status = body.status
    if new_status not in ALLOWED_STATUS:
        raise HTTPException(400, detail={"code": "invalid_status"})

    if new_status in ("Aprovado", "Reprovado"):
        await _apply_bling_decision_by_pedido(
            session,
            user.id,
            pedido_bling=pedido_bling,
            sku=body.sku,
            new_status=new_status,
            update_bling=not body.local_only,
        )
    else:
        # Pendente: reverter o status local, sem mexer no Bling
        await session.execute(
            update(BlingOrder)
            .where(BlingOrder.numero == pedido_bling)
            .values(status=new_status)
        )

    await session.commit()
    await _refresh_mv_silent(session)
    logger.info(
        "marketplace_status_patched",
        pedido_bling=pedido_bling,
        status=new_status,
        local_only=body.local_only,
    )
    return {"pedido_bling": pedido_bling, "status": new_status}


@router.patch("/{margem_id}", response_model=MargensOut)
async def patch_margens(
    margem_id: UUID,
    body: MargensPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("margem", "edit"))],
) -> MargensOut:
    row = (
        await session.execute(select(Margens).where(Margens.id == margem_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "margem_not_found"})

    data = body.model_dump(exclude_unset=True)
    local_only = bool(data.pop("local_only", False))
    new_status = data.get("status")
    if "status" in data and new_status is not None and new_status not in ALLOWED_STATUS:
        raise HTTPException(400, detail={"code": "invalid_status"})

    if new_status in ("Aprovado", "Reprovado"):
        await _apply_bling_decision(
            session,
            user.id,
            row,
            new_status,
            update_bling=not local_only,
        )

    for k, v in data.items():
        setattr(row, k, v)
    await session.commit()
    await session.refresh(row)
    logger.info(
        "margens_patched",
        margem_id=str(row.id),
        pedido_bling=row.pedido_bling,
        status=row.status,
    )
    return MargensOut.model_validate(row)


async def _apply_bling_decision(
    session: AsyncSession,
    actor_id: UUID,
    margem: Margens,
    new_status: str,
    *,
    update_bling: bool = True,
) -> None:
    await _apply_bling_decision_by_pedido(
        session,
        actor_id,
        pedido_bling=margem.pedido_bling,
        sku=margem.sku,
        new_status=new_status,
        update_bling=update_bling,
    )


async def _apply_bling_decision_by_pedido(
    session: AsyncSession,
    actor_id: UUID,
    *,
    pedido_bling: int | str | None,
    sku: str | None,
    new_status: str,
    update_bling: bool = True,
) -> None:
    if pedido_bling is None:
        raise HTTPException(400, detail={"code": "pedido_bling_missing"})

    order = await _find_bling_order_by_pedido(session, str(pedido_bling), sku)
    if order is None or order.bling_id is None:
        raise HTTPException(404, detail={"code": "bling_order_not_found"})

    situacao_id = SITUACAO_APROVADO if new_status == "Aprovado" else SITUACAO_REPROVADO
    current_situacao_id = order.situacao

    # Reprovar so patcheia o Bling quando o pedido esta em "Em aberto" (situacao 6).
    # Em qualquer outra situacao o pedido ja saiu do fluxo onde a reprovacao
    # faz sentido no Bling — entao atualizamos somente o status local.
    patch_bling = update_bling
    if new_status == "Reprovado" and str(current_situacao_id or "") != str(SITUACAO_APROVADO):
        patch_bling = False

    if patch_bling and str(current_situacao_id or "") != str(situacao_id):
        client = await _global_bling_client(session)
        if client is None:
            raise HTTPException(400, detail={"code": "bling_integration_missing"})

        if new_status == "Aprovado":
            steps: list[int] = []
            if str(current_situacao_id or "") != str(SITUACAO_ATENDIDO):
                steps.append(SITUACAO_ATENDIDO)
            steps.append(SITUACAO_APROVADO)
        else:
            steps = [SITUACAO_REPROVADO]

        for step_id in steps:
            try:
                await client.update_order_situacao(int(order.bling_id), step_id)
            except httpx.HTTPStatusError as e:
                code = e.response.status_code if e.response is not None else 0
                body = e.response.text[:500] if e.response is not None else ""
                message = _bling_error_message(e.response) if e.response is not None else None
                logger.warning(
                    "bling_situacao_patch_failed",
                    bling_id=order.bling_id,
                    situacao_id=step_id,
                    http=code,
                    body=body,
                )
                raise HTTPException(
                    502,
                    detail={
                        "code": "bling_patch_failed",
                        "http": code,
                        "message": message or "Falha ao atualizar situacao no Bling",
                    },
                ) from e
    else:
        logger.info(
            "bling_situacao_skipped",
            bling_id=order.bling_id,
            situacao_id=situacao_id,
            current_situacao=str(current_situacao_id or ""),
            new_status=new_status,
        )

    await session.execute(
        update(BlingOrder)
        .where(BlingOrder.bling_id == order.bling_id)
        .values(
            aprovado_por=actor_id,
            status=new_status,
            verificado=True,
            **({"situacao": str(situacao_id)} if patch_bling else {}),
        )
    )


async def _find_bling_order_by_pedido(
    session: AsyncSession,
    pedido_bling: str,
    sku: str | None,
) -> BlingOrder | None:
    stmt = (
        select(BlingOrder)
        .where(BlingOrder.numero == pedido_bling)
        .where(BlingOrder.bling_id.is_not(None))
    )
    if sku:
        stmt = stmt.where(BlingOrder.item_codigo == sku)
    return (
        await session.execute(stmt.order_by(BlingOrder.item_index.asc()).limit(1))
    ).scalar_one_or_none()


async def _global_bling_client(session: AsyncSession) -> BlingClient | None:
    integ = (
        await session.execute(
            select(Integration)
            .where(Integration.platform == IntegrationPlatform.BLING)
            .where(Integration.status == "active")
            .where(Integration.store_id.is_(None))
            .order_by(Integration.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if integ is None:
        return None
    return BlingClient(decrypt_json(integ.credentials), integration_id=integ.id)


def _bling_error_message(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300] or None
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        fields = error.get("fields")
        if isinstance(fields, list) and fields:
            first = fields[0]
            if isinstance(first, dict) and first.get("msg"):
                return str(first["msg"])
        for key in ("description", "message"):
            if error.get(key):
                return str(error[key])
    return None
