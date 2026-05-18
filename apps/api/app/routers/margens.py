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
SITUACAO_VERIFICAR_MARGEM = 84680
SITUACAO_VERIFICAR_MARGEM_NOME = "Verificar Margem"

# "Needs attention" flag — rows the user must triage. Three independent triggers:
#   1) margin below the configured minimum
#   2) seller paid more shipping than projected (negative frete result)
#   3) Bling-computed net diverges from marketplace net by more than 1%
# Rows that don't trigger any of these are treated as auto-approved in the UI
# (status filter "Pendente" hides them; "Aprovado" includes them).
_ATTENTION_MARGEM_SQL = (
    "(v.marketplace_margem IS NOT NULL AND v.margem_minima IS NOT NULL "
    " AND v.marketplace_margem < v.margem_minima)"
)
_ATTENTION_FRETE_SQL = (
    "(v.frete_resultado_item IS NOT NULL AND v.frete_resultado_item < 0)"
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
    # attention_type narrows which "needs attention" trigger qualifies a
    # Pendente row. Defaults to all triggers ORd together.
    attention_sql = _ATTENTION_TYPE_MAP.get(attention_type or "all", NEEDS_ATTENTION_SQL)
    if status:
        # Effective status:
        #   Aprovado/Reprovado in DB → respected as-is
        #   NULL/Pendente in DB     → derived from NEEDS_ATTENTION_SQL
        #                               (true → Pendente, false → Aprovado)
        if status == "Pendente":
            where.append(
                f"(v.bling_status_margem IS NULL OR v.bling_status_margem = 'Pendente') "
                f"AND {attention_sql}"
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
            CASE
                WHEN COALESCE(v.plataforma_bling, v.plataforma_financeiro) = 'shopee'
                THEN (v.evento_freight * v.item_proportion)
                ELSE v.marketplace_frete_real_cobrado_item
            END                                                  AS frete_plataforma,
            CASE
                WHEN COALESCE(v.plataforma_bling, v.plataforma_financeiro) = 'shopee'
                THEN (v.evento_frete_anuncio * v.item_proportion)
                ELSE v.marketplace_frete_item
            END                                                  AS frete_anuncio,
            v.frete_projetado_item                               AS frete_projetado,
            CASE
                WHEN COALESCE(v.plataforma_bling, v.plataforma_financeiro) = 'shopee'
                THEN 0::numeric
                ELSE (v.marketplace_frete_item - v.marketplace_frete_real_cobrado_item)
            END                                                  AS reembolso,
            CASE
                WHEN COALESCE(v.plataforma_bling, v.plataforma_financeiro) = 'shopee'
                THEN v.frete_projetado_item - (v.evento_frete_anuncio * v.item_proportion)
                ELSE v.frete_resultado_item
            END                                                  AS resultado_frete,
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
            bo.observacao
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

    return MargensMarketplacePage(
        items=[MargensMarketplaceOut.model_validate(dict(r)) for r in rows],
        total=int(total or 0),
        limit=limit,
        offset=offset,
        platforms=platforms,
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
    client: BlingClient | None = None
    if update_bling and (
        new_status == "Reprovado" or str(current_situacao_id or "") != str(situacao_id)
    ):
        client = await _global_bling_client(session)
        if client is None:
            raise HTTPException(400, detail={"code": "bling_integration_missing"})

        if new_status == "Reprovado":
            current_situacao_id, _current_situacao_nome = await _require_verificar_margem(
                client,
                int(order.bling_id),
            )

    if update_bling and str(current_situacao_id or "") != str(situacao_id):
        if client is None:
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
            "bling_situacao_already_target",
            bling_id=order.bling_id,
            situacao_id=situacao_id,
        )

    await session.execute(
        update(BlingOrder)
        .where(BlingOrder.bling_id == order.bling_id)
        .values(
            aprovado_por=actor_id,
            status=new_status,
            verificado=True,
            **({"situacao": str(situacao_id)} if update_bling else {}),
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


async def _require_verificar_margem(
    client: BlingClient,
    bling_order_id: int,
) -> tuple[str, str | None]:
    try:
        raw_order = await client.get_order(bling_order_id)
    except httpx.HTTPStatusError as e:
        code = e.response.status_code if e.response is not None else 0
        body = e.response.text[:500] if e.response is not None else ""
        message = _bling_error_message(e.response) if e.response is not None else None
        logger.warning(
            "bling_situacao_check_failed",
            bling_id=bling_order_id,
            http=code,
            body=body,
        )
        raise HTTPException(
            502,
            detail={
                "code": "bling_situacao_check_failed",
                "http": code,
                "message": message or "Nao foi possivel verificar a situacao atual no Bling.",
            },
        ) from e
    except httpx.HTTPError as e:
        logger.warning(
            "bling_situacao_check_failed",
            bling_id=bling_order_id,
            error=str(e),
        )
        raise HTTPException(
            502,
            detail={
                "code": "bling_situacao_check_failed",
                "message": "Nao foi possivel verificar a situacao atual no Bling.",
            },
        ) from e

    current_situacao_id, current_situacao_nome = _bling_order_situacao(raw_order)
    if current_situacao_id != str(SITUACAO_VERIFICAR_MARGEM):
        current_label = _format_situacao_label(current_situacao_id, current_situacao_nome)
        logger.info(
            "bling_reprovacao_blocked_by_situacao",
            bling_id=bling_order_id,
            current_situacao_id=current_situacao_id,
            current_situacao_nome=current_situacao_nome,
            required_situacao_id=SITUACAO_VERIFICAR_MARGEM,
        )
        raise HTTPException(
            409,
            detail={
                "code": "bling_situacao_not_verificar_margem",
                "current_situacao": current_situacao_id,
                "current_situacao_nome": current_situacao_nome,
                "required_situacao": str(SITUACAO_VERIFICAR_MARGEM),
                "message": (
                    f"O pedido esta em {current_label} no Bling. "
                    "Para reprovar, ele precisa estar em Verificar Margem. "
                    "Nenhuma alteracao foi enviada ao Bling."
                ),
            },
        )

    return current_situacao_id, current_situacao_nome


def _bling_order_situacao(raw_order: dict) -> tuple[str | None, str | None]:
    situacao = raw_order.get("situacao") if isinstance(raw_order, dict) else None
    if isinstance(situacao, dict):
        situacao_id = situacao.get("id")
        situacao_nome = (
            situacao.get("nome")
            or situacao.get("descricao")
            or situacao.get("valor")
            or situacao.get("name")
        )
        return (
            str(situacao_id) if situacao_id is not None else None,
            str(situacao_nome) if situacao_nome else None,
        )
    if situacao is not None:
        return str(situacao), None
    return None, None


def _format_situacao_label(situacao_id: str | None, situacao_nome: str | None) -> str:
    if situacao_nome and situacao_id:
        return f"{situacao_nome} ({situacao_id})"
    if situacao_nome:
        return situacao_nome
    if situacao_id:
        return f"situacao {situacao_id}"
    return "uma situacao desconhecida"


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
