"""Margens — per-order margin rows with approval status."""

from __future__ import annotations

from datetime import date
from io import BytesIO
from typing import Annotated
from uuid import UUID

import httpx
import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_admin, require_permission
from app.models import BlingOrder, Integration, IntegrationPlatform, Margens, User
from app.schemas.margens import (
    ALLOWED_STATUS,
    MargensMarketplaceOut,
    MargensMarketplacePage,
    MargensOut,
    MargensPatch,
    SaldoRaioXItem,
    SaldoRaioXOut,
)
from app.security.cipher import decrypt_json
from app.services.margem_audit import record_margem_audit
from app.services.rentabilidade_xlsx import build_rentabilidade_xlsx
from app.services.marketplaces.bling import BlingClient
from app.services.verificar_margem import (
    SNAPSHOT_TABLE as _VERIFICAR_MARGEM_TABLE,
)
from app.services.verificar_margem import (
    patch_financials_for_item_silent as _patch_verificar_margem_financials_silent,
)
from app.services.verificar_margem import (
    patch_status_for_pedido_silent as _patch_verificar_margem_status_silent,
)
from app.services.verificar_margem import (
    qualified_table as _qualified_table,
)
from app.services.verificar_margem import (
    rebuild_all as _rebuild_verificar_margem,
)
from app.services.verificar_margem import (
    refresh_for_pedido as _refresh_verificar_margem_for_pedido,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/api/margens", tags=["margens"])
_BLING_ORDERS_TABLE = _qualified_table("bling_orders")
_STORES_TABLE = _qualified_table("stores")
_SITUACAO_BLING_TABLE = _qualified_table("situacao_bling")
_PRICING_ACCOUNTS_TABLE = _qualified_table("pricing_accounts")

# Situações que compõem a rentabilidade (Em aberto / Em andamento / Entregue),
# mesmas da rotina diária `rentabilidade_valuation.py`.
_RENT_SITUACOES_SQL = "('6', '15', '83953')"

SITUACAO_APROVADO = 6
SITUACAO_ATENDIDO = 9
SITUACAO_REPROVADO = 83955
SITUACAO_ENVIADO_ETIQUETA = 83965
SITUACAO_AGUARDANDO_DEVOLUCAO = 83957

# Situações em que uma divergência de saldo é triada (vira "saldo divergente").
# 6 = "Em aberto" (pré-faturamento) e 83965 = "Enviado Etiqueta" (etiqueta
# gerada). Mantido como tupla de strings porque bling_orders.situacao é text.
_SITUACOES_SALDO_DIVERGENTE = (str(SITUACAO_APROVADO), str(SITUACAO_ENVIADO_ETIQUETA))
_SITUACOES_SALDO_DIVERGENTE_IN = ", ".join(f"'{s}'" for s in _SITUACOES_SALDO_DIVERGENTE)

# "Needs attention" flag — rows the user must triage. Three independent triggers:
#   1) margin below the configured minimum
#   2) seller paid more shipping than the listing/ad freight value
#   3) Bling-computed net diverges from marketplace net by more than R$0,01
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
# Frete Anuncio is intentionally not prorated: the listing/ad freight quote is
# attached to each product row at full value, even when the order has multiple
# items.
_FRETE_ANUNCIO_SQL = "v.evento_frete_anuncio"

# Frete Resultado (per-item) = Frete Plataforma − Frete Anuncio.
# Positive means the marketplace charged more than the listing/ad quote.
_FRETE_RESULTADO_SQL = f"(({_FRETE_PLATAFORMA_SQL}) - ({_FRETE_ANUNCIO_SQL}))"

# Margem baixa = margem abaixo da mínima configurada. Pedidos em "Aguardando
# Devolução" (situação 83957) são excluídos: a venda está em processo de
# devolução, então margem baixa ali não é algo a triar. IS DISTINCT FROM
# preserva linhas com situacao NULL (continuam contando como margem baixa).
_ATTENTION_MARGEM_SQL = (
    f"(v.marketplace_margem IS NOT NULL AND v.margem_minima IS NOT NULL "
    f" AND v.marketplace_margem < v.margem_minima "
    f" AND v.situacao IS DISTINCT FROM '{SITUACAO_AGUARDANDO_DEVOLUCAO}')"
)
_ATTENTION_FRETE_SQL = (
    f"(({_FRETE_ANUNCIO_SQL}) IS NOT NULL AND {_FRETE_RESULTADO_SQL} > 0)"
)
# "Saldo divergente" — restrito a pedidos em situação 6 ou 83965 (só esses
# contam para triagem), com saldo_base do Bling presente, e que satisfaçam UMA
# das condições:
#   a) Plataforma AINDA NULA (financeiro não reconciliado) → não auto-aprovar
#      antes de bater o repasse real do marketplace. Marketplaces como TikTok
#      liquidam ~1-2 semanas após a entrega, então o pedido fica "divergente"
#      (não resolvido) até o settlement chegar — aí vira mismatch (b) ou some.
#   b) Divergência real: |saldo_bling − saldo_plataforma| > R$0,01.
# O gatilho do botão "→" (copia marketplace→Bling) na UI continua exigindo
# Plataforma != null (não há o que copiar quando nula): margem.vue
# [6, 83965].includes(situacao_id) && saldo_plataforma != null &&
# Math.abs(saldo_plataforma - saldo_bling) > 0.01. Ou seja, linhas (a) entram na
# lista "saldo divergente" mas sem o botão de cópia (a ação é aguardar o
# settlement ou editar o Saldo Efetivo manualmente). Limiar absoluto de R$0,01
# (um relativo de 1% escondia gaps em itens caros, ex.: R$60 num Macbook de
# R$7.000 = 0,85%, mas ainda é divergência a corrigir).
_ATTENTION_SALDO_SQL = (
    f"(v.situacao IN ({_SITUACOES_SALDO_DIVERGENTE_IN}) "
    " AND v.bling_valorbase_item IS NOT NULL "
    " AND ("
    "       v.marketplace_liquido_base_margem_item IS NULL "
    "       OR ABS("
    "            (v.bling_valorbase_item"
    "             - COALESCE(v.bling_custofrete_item, 0)"
    "             - COALESCE(v.bling_taxacomissao_item, 0))"
    "            - v.marketplace_liquido_base_margem_item"
    "         ) > 0.01"
    " ))"
)

# "Needs attention" flag — rows the user must triage. Three independent triggers:
#   1) margin below the configured minimum
#   2) seller paid more shipping than the listing/ad freight value
#   3) Bling-computed net diverges from marketplace net by more than R$0,01
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

# Saldo "Bling" = líquido gravado no Bling (valor_base − frete − taxa). É a base
# do Saldo Efetivo. Como a edição inline grava valorbase/zera taxa/frete no
# snapshot (patch_financials_for_item), esta expressão recalcula sozinha após
# editar — por isso o Efetivo é ancorado no Bling e não no líquido do
# marketplace (que o patch não toca, ficando stale).
_SALDO_BLING_SQL = (
    "(v.bling_valorbase_item"
    " - COALESCE(v.bling_custofrete_item, 0)"
    " - COALESCE(v.bling_taxacomissao_item, 0))"
)

# Saldo Plataforma. Normalmente = líquido reconciliado do marketplace
# (v.marketplace_liquido_base_margem_item). Na Amazon esse líquido só chega
# DEPOIS do envio (fica NULL antes), então projetamos o saldo:
#   valor da venda − frete projetado − comissão projetada da loja
# onde a comissão projetada = valor da venda × commission (fração 0.19 = 19%)
# do cadastro da conta na Tabela de Preços (pricing_accounts.commission,
# join por pricing_account_id → pa_comm). Assim que o líquido real chega,
# o COALESCE volta a usá-lo (a projeção é só fallback pré-envio). Só Amazon.
_SALDO_PLATAFORMA_SQL = (
    "COALESCE(v.marketplace_liquido_base_margem_item,"
    " CASE WHEN COALESCE(v.plataforma_bling, v.plataforma_financeiro) = 'amazon'"
    "           AND v.bling_valorbase_item IS NOT NULL"
    "      THEN (v.bling_valorbase_item"
    "            - COALESCE(v.frete_projetado_item, 0)"
    "            - (v.bling_valorbase_item * COALESCE(pa_comm.commission, 0)))"
    "      ELSE NULL END)"
)

# Ajuste por item: SÓ o reembolso entra no Saldo Efetivo. O `prejuizo` da tabela
# refunds é referência visual (base pra abrir o chamado de reembolso) e NUNCA
# desconta do saldo/margem — inclusive porque o frete que o prejuizo de Logística
# representa já está embutido no líquido do Bling (valor_base − frete − taxa), e
# descontá-lo de novo contaria duas vezes. `v.ajustes` já é
# SUM(refunds.reembolso) * item_proportion (migration 0079) e pode ser + ou −.
# (NÃO usar v.saldo_final − saldo_plataforma: esse delta embute −prejuizo.)
_REEMBOLSO_DELTA_SQL = "v.ajustes"

# Saldo Final ancorado no Bling = Saldo Efetivo (Bling) + reembolso.
# Recalcula após edição (o snapshot mantém ajustes, então o delta é estável, e
# _SALDO_BLING_SQL reflete o valor recém-gravado).
_SALDO_FINAL_BLING_SQL = (
    f"({_SALDO_BLING_SQL} + COALESCE({_REEMBOLSO_DELTA_SQL}, 0))"
)


def _build_marketplace_items_sql(source_table: str, where_sql: str, *, paginate: bool) -> str:
    """Builds the per-item SELECT against either the snapshot table or the
    full view (`vw_conciliacao_margens_marketplace_all`). Columns are
    identical between sources, so the same shape is returned to the client.
    """
    limit_clause = "LIMIT :limit OFFSET :offset" if paginate else ""
    sql = f"""
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
            {_FRETE_ANUNCIO_SQL}                                 AS frete_anuncio,
            v.frete_projetado_item                               AS frete_projetado,
            CASE
                WHEN COALESCE(v.plataforma_bling, v.plataforma_financeiro) = 'shopee'
                THEN 0::numeric
                ELSE (v.marketplace_frete_item - v.marketplace_frete_real_cobrado_item)
            END                                                  AS reembolso,
            {_FRETE_RESULTADO_SQL}                               AS resultado_frete,
            {_SALDO_PLATAFORMA_SQL}                              AS saldo_plataforma,
            {_SALDO_BLING_SQL}                                   AS saldo_bling,
            -- Saldo Efetivo = saldo realizado do item, ancorado no Bling
            -- (valor_base − frete − taxa), NÃO no líquido do marketplace. É lá
            -- que a edição inline grava (POST /sync-saldo-final →
            -- bling_orders.valorbase, zerando taxa/frete) e onde o snapshot é
            -- patcheado, então o valor digitado aparece na hora. O líquido do
            -- marketplace continua na coluna Saldo Plataforma.
            {_SALDO_BLING_SQL}                                   AS saldo_efetivo,
            v.marketplace_margem                                 AS margem,
            -- margem_bling já vem perdimento-aware da view/snapshot
            -- (migration 0142): Perdimento (83956) não desconta o custo de
            -- novo, então a coluna armazenada já reflete a regra correta.
            v.bling_margem_calculado                             AS margem_bling,
            v.margem_minima,
            -- Margem Pós Reembolso = margem recalculada já com o reembolso da
            -- tabela refunds aplicado, sobre o Saldo Final ancorado no Bling
            -- (_SALDO_FINAL_BLING_SQL = saldo Bling + reembolso; o prejuizo NÃO
            -- desconta). Como usa o saldo do Bling, recalcula após a edição
            -- inline. Quando há reembolso negativo (manutenção/devolução) fica
            -- abaixo da "Margem" (que ignora o reembolso).
            CASE
                WHEN COALESCE(v.bling_custo_produtos, 0) > 0
                     AND {_SALDO_FINAL_BLING_SQL} IS NOT NULL
                THEN ({_SALDO_FINAL_BLING_SQL} - v.bling_custo_produtos)
                     / v.bling_custo_produtos
                ELSE NULL::numeric
            END                                                  AS margem_pos_reembolso,
            v.situacao                                           AS situacao_id,
            v.situacao_nome                                      AS situacao,
            v.ajustes,
            {_SALDO_FINAL_BLING_SQL}                             AS saldo_final,
            CASE
                -- 'Pendente' gravado = hold manual (edição de Saldo Efetivo com
                -- margem recalculada abaixo da mínima); fixa o pedido como
                -- Pendente mesmo sem gatilho de atenção ativo.
                WHEN v.bling_status_margem IN ('Aprovado', 'Reprovado', 'Pendente')
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
        FROM {source_table} v
        LEFT JOIN {_PRICING_ACCOUNTS_TABLE} pa_comm
            ON pa_comm.id = v.pricing_account_id
        LEFT JOIN LATERAL (
            SELECT bo.observacao
            FROM {_BLING_ORDERS_TABLE} bo
            WHERE bo.numero = v.pedido_bling
              AND bo.observacao IS NOT NULL
            LIMIT 1
        ) bo ON TRUE
        WHERE {where_sql}
        ORDER BY v.data DESC NULLS LAST, v.pedido_bling DESC, v.bling_order_item_id
        {limit_clause}
    """  # noqa: S608
    return sql


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

    Reads from davinci.verificar_margem (snapshot of the view).
    The snapshot is repopulated every 30min by the worker cron, fully
    rebuilt by the 'atualizar' UI button. User-facing PATCHes update the
    affected snapshot columns directly so edits appear immediately.
    """
    # "Cancelado" (12) e "Aguardando Cancelamento" (83955) ficam fora do fluxo de
    # triagem da margem: são pedidos cancelados / em processo de cancelamento e
    # não devem ser aprovados/reprovados. 83955 é justamente o destino de um
    # pedido reprovado no Bling (SITUACAO_REPROVADO) — o estado é transitório até
    # virar "Cancelado". Filtrado por código (situacao) porque situacao_nome vem
    # de um JOIN e pode ser NULL; IS DISTINCT FROM preserva linhas com situacao NULL.
    #
    # EXCEÇÃO (auto-hold, 21/08): pedido segurado pelo robô da Margem também
    # está em 83955, mas com bling_status_margem='Pendente' GRAVADO — esse
    # PRECISA continuar visível, senão ninguém consegue analisá-lo (Aprovar/
    # Reprovar). Os 83955 dos outros fluxos seguem fora: reprovado no clique
    # (status='Reprovado') e falta de estoque (status NULL).
    where = [
        "v.situacao_nome != 'Cancelado'",
        f"(v.situacao IS DISTINCT FROM '{SITUACAO_REPROVADO}'"
        " OR v.bling_status_margem = 'Pendente')",
        f"NOT {_ATTENTION_FRETE_SQL}",
    ]
    params: dict = {"limit": limit, "offset": offset}
    if platform:
        where.append("COALESCE(v.plataforma_bling, v.plataforma_financeiro) = :platform")
        params["platform"] = platform
    if conta:
        where.append("v.loja_nome = :conta")
        params["conta"] = conta
    # attention_type narrows which "needs attention" trigger qualifies a row
    # (frete/margem/saldo). It is ANDed with the status filter, so e.g.
    # status=Pendente + attention_type=saldo returns only pending rows that are
    # saldo-divergent — matching the "Pendentes (30d)" workflow. When
    # attention_type='all' (default), status alone drives the filter.
    attention_sql = _ATTENTION_TYPE_MAP.get(attention_type or "all", NEEDS_ATTENTION_SQL)
    attention_active = attention_type and attention_type != "all"
    if attention_active:
        where.append(attention_sql)
    if status:
        # Effective status:
        #   Aprovado/Reprovado/Pendente in DB → respected as-is
        #   NULL in DB                        → derived from NEEDS_ATTENTION_SQL
        #                                        (true → Pendente, false → Aprovado)
        # 'Pendente' gravado = hold manual (edição de Saldo Efetivo com margem
        # recalculada abaixo da mínima): fixa o pedido na aba Pendente mesmo que
        # ele não dispare mais nenhum gatilho de atenção (ex.: divergência de
        # saldo resolvida pela própria edição).
        if status == "Pendente":
            where.append(
                f"(v.bling_status_margem = 'Pendente' "
                f" OR (v.bling_status_margem IS NULL AND {NEEDS_ATTENTION_SQL}))"
            )
        elif status == "Aprovado":
            where.append(
                "(v.bling_status_margem = 'Aprovado' "
                f" OR (v.bling_status_margem IS NULL AND NOT {NEEDS_ATTENTION_SQL}))"
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
        f"SELECT count(*) FROM {_VERIFICAR_MARGEM_TABLE} v WHERE {where_sql}"  # noqa: S608
    )
    platforms_sql = text(
        "SELECT DISTINCT COALESCE(plataforma_bling, plataforma_financeiro) AS p "  # noqa: S608
        f"FROM {_VERIFICAR_MARGEM_TABLE} "
        "WHERE COALESCE(plataforma_bling, plataforma_financeiro) IS NOT NULL "
        "ORDER BY 1"
    )
    contas_sql = text(
        "SELECT DISTINCT loja_nome "  # noqa: S608
        f"FROM {_VERIFICAR_MARGEM_TABLE} "
        "WHERE loja_nome IS NOT NULL "
        "ORDER BY 1"
    )
    items_sql = text(
        _build_marketplace_items_sql(_VERIFICAR_MARGEM_TABLE, where_sql, paginate=True)
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


@router.get("/rentabilidade/export.xlsx")
async def export_rentabilidade(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_admin)],
    inicio: date = Query(..., description="data inicial (YYYY-MM-DD, fuso SP)"),
    fim: date = Query(..., description="data final (YYYY-MM-DD, fuso SP)"),
) -> StreamingResponse:
    """Planilha de rentabilidade por pedido no período (abas Pedidos + Resumo).

    Agrega `bling_orders` (situações 6/15/83953) por pedido com a fórmula da
    rentabilidade diária — base/comissão/frete = MAX, custo = SUM(preco_custo×
    quantidade) — recortada pela data de realização (fuso SP). Sem coluna de
    categoria. Admin-only porque expõe custo e lucro por pedido (a coluna
    "Lucro" da própria página também é restrita a admin).
    """
    if fim < inicio:
        raise HTTPException(status_code=400, detail="data final anterior à inicial")
    if (fim - inicio).days > 366:
        raise HTTPException(status_code=400, detail="período máximo de 1 ano")

    rows_sql = text(  # noqa: S608 — tabelas vêm de _qualified_table, sem input do usuário
        f"""
        WITH ped AS (
            SELECT numero,
                   MIN((data AT TIME ZONE 'America/Sao_Paulo')::date) AS data_sp,
                   MAX(COALESCE(numeroloja, '')) AS numeroloja,
                   MAX(COALESCE(loja, ''))       AS loja,
                   MAX(situacao::text)           AS situacao,
                   MAX(COALESCE(valorbase, 0))    AS base,
                   MAX(COALESCE(taxacomissao, 0)) AS com,
                   MAX(COALESCE(custofrete, 0))   AS frete,
                   SUM(COALESCE(preco_custo, 0) * COALESCE(item_quantidade, 0)) AS custo
            FROM {_BLING_ORDERS_TABLE}
            WHERE situacao IN {_RENT_SITUACOES_SQL}
              AND (data AT TIME ZONE 'America/Sao_Paulo')::date BETWEEN :inicio AND :fim
            GROUP BY numero
        )
        SELECT p.data_sp, p.numero, p.numeroloja, p.loja, p.situacao,
               COALESCE(s.nome, p.situacao) AS situacao_nome,
               p.base, p.com, p.frete, p.custo
        FROM ped p
        LEFT JOIN {_SITUACAO_BLING_TABLE} s ON s.id::text = p.situacao
        ORDER BY p.data_sp, p.numero
        """
    )
    rows = (
        await session.execute(rows_sql, {"inicio": inicio, "fim": fim})
    ).mappings().all()

    stores_sql = text(  # noqa: S608
        f"SELECT bling_store_id, marketplace, apelido_override "
        f"FROM {_STORES_TABLE} WHERE bling_store_id IS NOT NULL"
    )
    store_map: dict[str, str] = {}
    for bid, mk, ap in (await session.execute(stores_sql)).all():
        ap = (ap or "").strip()
        mk = (mk or "").strip()
        if mk and ap and mk.lower() not in ap.lower():
            label = f"{mk} · {ap}"
        else:
            label = ap or mk or str(bid)
        store_map[str(bid)] = label

    buf = build_rentabilidade_xlsx(rows, store_map, inicio, fim)
    fname = f"rentabilidade_{inicio.isoformat()}_{fim.isoformat()}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/marketplace/lookup", response_model=MargensMarketplacePage)
async def lookup_marketplace(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("margem", "view"))],
    pedido: Annotated[str, Query(min_length=1)],
    force_refresh: bool = Query(False),
) -> MargensMarketplacePage:
    """Busca pontual por um numero de pedido (Bling ou marketplace).

    Fluxo em duas etapas:
      1. Default (force_refresh=False): le apenas do snapshot
         `verificar_margem` (fast path, milissegundos). Se vazio mas o
         pedido existe em `bling_orders`, retorna
         `historico_disponivel=True` para o frontend exibir um CTA.
      2. Com force_refresh=True (clique no CTA): dispara
         `refresh_for_pedido`, que le da view completa
         `vw_conciliacao_margens_marketplace_all`. A view nao permite
         pushdown do filtro por causa dos CTEs agregados, entao essa
         etapa pode levar ~3 minutos.

    Sem o filtro `situacao_nome != 'Cancelado'` — em lookup pontual o
    usuario quer ver o pedido independente da situacao (inclusive
    cancelado).
    """
    pedido_clean = pedido.strip()
    if not pedido_clean:
        raise HTTPException(400, detail={"code": "pedido_required"})

    where_sql = "(v.pedido_bling = :pedido OR v.pedido_marketplace = :pedido)"
    params = {"pedido": pedido_clean}
    items_sql = text(
        _build_marketplace_items_sql(_VERIFICAR_MARGEM_TABLE, where_sql, paginate=False)
    )

    async def _read_snapshot() -> list:
        return list((await session.execute(items_sql, params)).mappings().all())

    rows = await _read_snapshot()

    # `pedido_bling` alvo de um eventual refresh: derivado do snapshot quando já
    # há linha (cobre busca por numero de marketplace, cujo pedido_bling vem na
    # linha) ou resolvido em bling_orders quando o snapshot está vazio.
    refresh_key: str | None = (
        str(rows[0]["pedido_bling"]) if rows and rows[0].get("pedido_bling") else None
    )
    if refresh_key is None:
        bling_numero_row = (await session.execute(
            text(
                "SELECT numero FROM davinci.bling_orders "
                "WHERE numero = :p OR numeroloja = :p LIMIT 1"
            ),
            {"p": pedido_clean},
        )).first()
        refresh_key = (
            str(bling_numero_row[0]) if bling_numero_row and bling_numero_row[0] else None
        )

    if force_refresh and refresh_key:
        # Slow path — opt-in via CTA. Re-lê da view completa (sem janela de 20d)
        # para o snapshot e re-consulta. Roda MESMO quando já existe linha no
        # snapshot, para atualizar pedidos antigos cujo cache ficou velho (ex.:
        # reembolso lançado depois, fora da janela de 30 dias).
        await _refresh_verificar_margem_for_pedido(session, refresh_key)
        rows = await _read_snapshot()

    # "Dá pra forçar refresh deste pedido" — true sempre que o pedido existe no
    # Bling, inclusive com linha presente, para o frontend oferecer o refresh
    # mesmo de pedidos fora da janela (snapshot possivelmente defasado).
    historico_disponivel = bool(refresh_key)

    items = [MargensMarketplaceOut.model_validate(dict(r)) for r in rows]
    return MargensMarketplacePage(
        items=items,
        total=len(items),
        limit=len(items),
        offset=0,
        platforms=[],
        contas=[],
        historico_disponivel=historico_disponivel,
    )


class MarketplaceObsPatch(BaseModel):
    observacao: str | None = None


class SaldoFinalPatch(BaseModel):
    valor_base: float | None = None


@router.get("/marketplace/saldo-detalhe/{pedido_bling}", response_model=SaldoRaioXOut)
async def saldo_detalhe_marketplace(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("margem", "view"))],
    pedido_bling: str,
) -> SaldoRaioXOut:
    """Raio-X do saldo do pedido: a "foto" que explica os dois números da tela.

    Lado Bling = o pedido de venda como está gravado lá (valor base − frete −
    taxa de comissão). Lado plataforma = o repasse que o marketplace informou
    (bruto − taxas − frete ± desconto/reembolso/imposto/ajuste = líquido). Lê
    SÓ o snapshot (mesma fonte da listagem), então bate com a tela e abre na
    hora — sem chamada externa. Por item (packs rateiam via item_proportion) e
    totais do pedido; inclui os componentes da projeção Amazon quando o número
    exibido é projetado (líquido real ainda não veio)."""
    sql = f"""
        SELECT
            v.pedido_bling,
            v.pedido_marketplace,
            COALESCE(v.plataforma_bling, v.plataforma_financeiro) AS plataforma,
            v.loja_nome AS conta,
            v.data,
            v.situacao_nome AS situacao,
            v.sku, v.produto, v.quantidade, v.item_proportion,
            v.bling_valorbase_item, v.bling_custofrete_item,
            v.bling_taxacomissao_item,
            {_SALDO_BLING_SQL}      AS saldo_bling_item,
            {_SALDO_PLATAFORMA_SQL} AS saldo_plataforma_item,
            v.bling_valorbase_pedido, v.bling_custofrete_pedido,
            v.bling_taxacomissao_pedido,
            v.marketplace_valor_bruto_pedido, v.marketplace_taxas_pedido,
            v.marketplace_frete_pedido, v.marketplace_rebate_pedido,
            v.marketplace_desconto_pedido, v.marketplace_reembolso_pedido,
            v.marketplace_imposto_pedido, v.marketplace_ajuste_pedido,
            v.marketplace_liquido_base_margem_pedido,
            v.financeiro_atualizado_em,
            v.frete_projetado_item,
            pa_comm.commission AS pa_commission,
            v.ajustes
        FROM {_VERIFICAR_MARGEM_TABLE} v
        LEFT JOIN {_PRICING_ACCOUNTS_TABLE} pa_comm
            ON pa_comm.id = v.pricing_account_id
        WHERE v.pedido_bling = :pedido
        ORDER BY v.sku NULLS LAST
    """  # noqa: S608
    rows = (await session.execute(text(sql), {"pedido": pedido_bling})).mappings().all()
    if not rows:
        raise HTTPException(404, detail={"code": "pedido_nao_encontrado"})

    def _f(v: object) -> float | None:
        return float(v) if v is not None else None  # type: ignore[arg-type]

    head = rows[0]
    itens = [
        SaldoRaioXItem(
            sku=r["sku"],
            produto=r["produto"],
            quantidade=r["quantidade"],
            proporcao=_f(r["item_proportion"]),
            bling_valorbase=_f(r["bling_valorbase_item"]),
            bling_custofrete=_f(r["bling_custofrete_item"]),
            bling_taxacomissao=_f(r["bling_taxacomissao_item"]),
            saldo_bling=_f(r["saldo_bling_item"]),
            saldo_plataforma=_f(r["saldo_plataforma_item"]),
        )
        for r in rows
    ]

    # Totais do pedido. Saldo Bling = a mesma conta da tela, no agregado.
    valorbase = _f(head["bling_valorbase_pedido"])
    custofrete = _f(head["bling_custofrete_pedido"])
    taxacomissao = _f(head["bling_taxacomissao_pedido"])
    saldo_bling = (
        valorbase - (custofrete or 0) - (taxacomissao or 0)
        if valorbase is not None
        else None
    )
    # Saldo plataforma do pedido: líquido real quando existe; senão a soma das
    # projeções por item (Amazon pré-envio); senão None (aguardando saldo).
    mp_liquido = _f(head["marketplace_liquido_base_margem_pedido"])
    saldo_plataforma = mp_liquido
    if saldo_plataforma is None:
        proj = [i.saldo_plataforma for i in itens if i.saldo_plataforma is not None]
        saldo_plataforma = sum(proj) if proj else None
    projecao_amazon = (
        mp_liquido is None
        and saldo_plataforma is not None
        and (head["plataforma"] or "") == "amazon"
    )
    proj_frete_vals = [
        _f(r["frete_projetado_item"])
        for r in rows
        if r["frete_projetado_item"] is not None
    ]
    ajustes_vals = [_f(r["ajustes"]) for r in rows if r["ajustes"] is not None]
    return SaldoRaioXOut(
        pedido_bling=str(head["pedido_bling"]),
        pedido_marketplace=head["pedido_marketplace"],
        plataforma=head["plataforma"],
        conta=head["conta"],
        data=head["data"],
        situacao=head["situacao"],
        bling_valorbase=valorbase,
        bling_custofrete=custofrete,
        bling_taxacomissao=taxacomissao,
        saldo_bling=saldo_bling,
        mp_valor_bruto=_f(head["marketplace_valor_bruto_pedido"]),
        mp_taxas=_f(head["marketplace_taxas_pedido"]),
        mp_frete=_f(head["marketplace_frete_pedido"]),
        mp_rebate=_f(head["marketplace_rebate_pedido"]),
        mp_desconto=_f(head["marketplace_desconto_pedido"]),
        mp_reembolso=_f(head["marketplace_reembolso_pedido"]),
        mp_imposto=_f(head["marketplace_imposto_pedido"]),
        mp_ajuste=_f(head["marketplace_ajuste_pedido"]),
        mp_liquido=mp_liquido,
        mp_atualizado_em=head["financeiro_atualizado_em"],
        saldo_plataforma=saldo_plataforma,
        projecao_amazon=bool(projecao_amazon),
        proj_frete_projetado=(
            sum(proj_frete_vals) if projecao_amazon and proj_frete_vals else None
        ),
        proj_comissao_frac=_f(head["pa_commission"]) if projecao_amazon else None,
        reembolso_ajustes=sum(ajustes_vals) if ajustes_vals else None,
        diferenca=(
            saldo_bling - saldo_plataforma
            if saldo_bling is not None and saldo_plataforma is not None
            else None
        ),
        itens=itens,
    )


@router.patch("/marketplace/observacao/{pedido_bling}")
async def patch_marketplace_observacao(
    pedido_bling: str,
    body: MarketplaceObsPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("margem", "edit"))],
) -> dict:
    """Salva observacao em todas as linhas (itens) do pedido em bling_orders."""
    next_value = (body.observacao or "").strip() or None
    prev_value = (
        await session.execute(
            select(BlingOrder.observacao).where(BlingOrder.numero == pedido_bling).limit(1)
        )
    ).scalar_one_or_none()
    result = await session.execute(
        update(BlingOrder)
        .where(BlingOrder.numero == pedido_bling)
        .values(observacao=next_value)
    )
    if result.rowcount == 0:
        raise HTTPException(404, detail={"code": "bling_order_not_found"})
    await record_margem_audit(
        session,
        acao="observacao",
        pedido_bling=pedido_bling,
        valor_antigo=prev_value,
        valor_novo=next_value,
        origem="margens",
        mudado_por=user.id,
    )
    await session.commit()
    logger.info(
        "marketplace_observacao_patched",
        pedido_bling=pedido_bling,
        rows=result.rowcount,
    )
    return {"pedido_bling": pedido_bling, "observacao": next_value, "rows": result.rowcount}


async def _update_bling_item_resilient(
    session: AsyncSession,
    *,
    bling_order_item_id: UUID,
    pedido_bling: str | None,
    sku: str | None,
    values: dict,
) -> bool:
    """UPDATE de bling_orders para um item da página de margens, curando um
    PK de snapshot defasado.

    O snapshot `verificar_margem` guarda `bling_orders.id` (um uuid4 que
    troca a cada re-ingest, porque `upsert_order` faz DELETE+INSERT). Se o
    pedido foi re-ingerido depois que a página/snapshot foi montada, o `id`
    cacheado não casa mais e o UPDATE tocaria 0 linhas → o usuário via
    `bling_order_not_found` numa ação que deveria funcionar. Quando isso
    acontece, re-resolve pela identidade estável do pedido (numero +
    item_codigo; no snapshot `sku` == `bling_orders.item_codigo`) e repete.
    Retorna True se alguma linha foi atualizada.
    """
    result = await session.execute(
        update(BlingOrder).where(BlingOrder.id == bling_order_item_id).values(**values)
    )
    if result.rowcount:
        return True
    if pedido_bling is None:
        return False
    target = await _find_bling_order_by_pedido(session, str(pedido_bling), sku)
    if target is None:
        return False
    result = await session.execute(
        update(BlingOrder).where(BlingOrder.id == target.id).values(**values)
    )
    return bool(result.rowcount)


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
              v.pedido_bling,
              v.sku
            FROM {_VERIFICAR_MARGEM_TABLE} v
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

    updated = await _update_bling_item_resilient(
        session,
        bling_order_item_id=bling_order_item_id,
        pedido_bling=row.pedido_bling,
        sku=row.sku,
        values={
            "valorbase": row.valorbase,
            "taxacomissao": row.taxacomissao,
            "custofrete": row.custofrete,
        },
    )
    if not updated:
        raise HTTPException(404, detail={"code": "bling_order_not_found"})
    await _patch_verificar_margem_financials_silent(
        session,
        bling_order_item_id=str(bling_order_item_id),
        valorbase=row.valorbase,
        taxacomissao=row.taxacomissao,
        custofrete=row.custofrete,
    )
    await session.commit()

    logger.info(
        "marketplace_synced_to_bling",
        bling_order_item_id=str(bling_order_item_id),
        pedido_bling=row.pedido_bling,
        valorbase=str(row.valorbase),
        taxacomissao=str(row.taxacomissao),
        custofrete=str(row.custofrete),
    )
    return {
        "ok": True,
        "valorbase": float(row.valorbase) if row.valorbase is not None else None,
        "taxacomissao": float(row.taxacomissao) if row.taxacomissao is not None else None,
        "custofrete": float(row.custofrete) if row.custofrete is not None else None,
    }


@router.post("/marketplace/{bling_order_item_id}/sync-saldo-final")
async def sync_bling_from_saldo_final(
    bling_order_item_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("margem", "edit"))],
    body: Annotated[SaldoFinalPatch | None, Body()] = None,
) -> dict:
    """One-shot apply: grava o Saldo Final como `valor_base` no Bling.

    Sem body, usa o `saldo_final` calculado (saldo efetivo do marketplace
    ajustado por prejuizo/reembolso da tabela refunds). Com `valor_base` no
    body, usa o valor editado manualmente pelo usuario. Em ambos os casos
    zera custofrete e taxacomissao para que (valor_base − taxa − frete) seja
    a renda final.
    """
    override = body.valor_base if body is not None else None

    row = (await session.execute(
        text(
            f"""
            SELECT v.saldo_final, v.pedido_bling, v.sku, v.bling_valorbase_item
            FROM {_VERIFICAR_MARGEM_TABLE} v
            WHERE v.bling_order_item_id = :id
            LIMIT 1
            """  # noqa: S608
        ),
        {"id": str(bling_order_item_id)},
    )).first()
    if row is None:
        raise HTTPException(404, detail={"code": "row_not_found"})

    valorbase = override if override is not None else row.saldo_final
    if valorbase is None:
        raise HTTPException(400, detail={"code": "no_saldo_final"})

    updated = await _update_bling_item_resilient(
        session,
        bling_order_item_id=bling_order_item_id,
        pedido_bling=row.pedido_bling,
        sku=row.sku,
        values={
            "valorbase": valorbase,
            "taxacomissao": 0,
            "custofrete": 0,
        },
    )
    if not updated:
        raise HTTPException(404, detail={"code": "bling_order_not_found"})
    await _patch_verificar_margem_financials_silent(
        session,
        bling_order_item_id=str(bling_order_item_id),
        valorbase=valorbase,
        taxacomissao=0,
        custofrete=0,
    )
    _old_vb = row.bling_valorbase_item
    await record_margem_audit(
        session,
        acao="saldo_final",
        pedido_bling=str(row.pedido_bling),
        sku=row.sku,
        valor_antigo=None if _old_vb is None else f"{float(_old_vb):.2f}",
        valor_novo=f"{float(valorbase):.2f}",
        origem="margens",
        mudado_por=user.id,
    )
    # A edição do Saldo Efetivo só aprova automaticamente quando a margem
    # recalculada (a partir do novo saldo, já patcheado acima) atinge a mínima.
    # `margem_final` espelha a coluna "Margem" da UI (margemFinal): pós-reembolso
    # quando há ajuste, senão a margem do Bling. Lê do snapshot DEPOIS do patch
    # financeiro, então reflete exatamente o valor recém-gravado.
    margin_row = (await session.execute(
        text(
            f"""
            SELECT
                v.margem_minima AS margem_minima,
                COALESCE(
                    NULLIF(
                        CASE
                            WHEN COALESCE(v.bling_custo_produtos, 0) > 0
                                 AND {_SALDO_FINAL_BLING_SQL} IS NOT NULL
                            THEN ({_SALDO_FINAL_BLING_SQL} - v.bling_custo_produtos)
                                 / v.bling_custo_produtos
                            ELSE NULL::numeric
                        END, 0),
                    v.bling_margem_calculado
                ) AS margem_final
            FROM {_VERIFICAR_MARGEM_TABLE} v
            WHERE v.bling_order_item_id = :id
            LIMIT 1
            """  # noqa: S608
        ),
        {"id": str(bling_order_item_id)},
    )).first()
    margem_final = margin_row.margem_final if margin_row is not None else None
    margem_minima = margin_row.margem_minima if margin_row is not None else None
    # Sem mínima cadastrada ou margem incalculável (sem custo) → não há critério
    # para barrar: mantém o comportamento antigo (aprova). Espelha a triagem, que
    # só marca "margem baixa" quando margem E mínima existem.
    clears_minimum = (
        margem_minima is None
        or margem_final is None
        or margem_final >= margem_minima
    )

    if clears_minimum:
        # Margem ok → aprova o pedido no DaVinci (status='Aprovado',
        # verificado=True). update_bling=False: o pedido já foi patcheado
        # financeiramente acima, não mexemos na situação do Bling aqui.
        await _apply_bling_decision_by_pedido(
            session,
            user.id,
            pedido_bling=row.pedido_bling,
            sku=row.sku,
            new_status="Aprovado",
            update_bling=False,
        )
        result_status = "Aprovado"
    else:
        # Margem abaixo da mínima → NÃO aprova automaticamente; o pedido
        # fica/permanece Pendente para decisão manual (sem tocar no Bling).
        await _mark_pedido_pendente_by_pedido(
            session,
            pedido_bling=row.pedido_bling,
            sku=row.sku,
        )
        result_status = "Pendente"
    await session.commit()

    logger.info(
        "saldo_final_synced_to_bling",
        bling_order_item_id=str(bling_order_item_id),
        pedido_bling=row.pedido_bling,
        valorbase=str(valorbase),
        manual=override is not None,
        status=result_status,
        margem=None if margem_final is None else float(margem_final),
        margem_minima=None if margem_minima is None else float(margem_minima),
    )
    return {
        "ok": True,
        "valorbase": float(valorbase),
        "taxacomissao": 0.0,
        "custofrete": 0.0,
        "status": result_status,
        "margem": None if margem_final is None else float(margem_final),
        "margem_minima": None if margem_minima is None else float(margem_minima),
    }


# Rebuild da view de conciliação é caro (~minutos em prod). Coordenamos via
# Redis para (a) pular se o snapshot foi reconstruído há pouco (freshness) e
# (b) garantir single-flight — um rebuild por vez, em vez de cada page-load
# dos usuários empilhar um rebuild de minutos.
_REFRESH_LOCK_KEY = "margem:snapshot:refresh:lock"
_REFRESH_FRESH_KEY = "margem:snapshot:refresh:fresh"
_REFRESH_LOCK_TTL_S = 600  # safety: libera o lock se o rebuild morrer no meio


@router.post("/marketplace/refresh")
async def refresh_marketplace_mv(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("margem", "view"))],
    max_age_s: int = Query(
        0,
        ge=0,
        le=3600,
        description=(
            "Pula o rebuild se o snapshot foi reconstruído há menos de N "
            "segundos. 0 (default, botão manual) sempre reconstrói."
        ),
    ),
) -> dict:
    """Rebuild davinci.verificar_margem on-demand.

    Botão manual ('atualizar') chama sem `max_age_s` → sempre reconstrói.
    O auto-refresh da página passa `max_age_s=300` → o servidor pula se já
    estiver fresco (throttle autoritativo, cross-usuário) e nunca roda dois
    rebuilds simultâneos (single-flight via lock Redis).
    """
    from app.redis_client import redis

    # Freshness: alguém reconstruiu dentro da janela → não refaz.
    if max_age_s > 0 and await redis.get(_REFRESH_FRESH_KEY):
        return {"refreshed": False, "skipped": "fresh", "rows": 0}

    # Single-flight: só um rebuild por vez. Demais retornam de imediato e o
    # cliente segue com o snapshot atual (que o rebuild em curso vai atualizar).
    got_lock = await redis.set(_REFRESH_LOCK_KEY, "1", nx=True, ex=_REFRESH_LOCK_TTL_S)
    if not got_lock:
        return {"refreshed": False, "skipped": "in_progress", "rows": 0}

    try:
        inserted = await _rebuild_verificar_margem(session)
    finally:
        await redis.delete(_REFRESH_LOCK_KEY)

    # Marca o snapshot como fresco pela janela do chamador (default 5min para
    # coalescer rajadas mesmo quando o gatilho foi o botão manual).
    await redis.set(_REFRESH_FRESH_KEY, "1", ex=max_age_s if max_age_s > 0 else 300)

    # Snapshot fresco → auto-hold dos pendentes "Em aberto" (Aguardando
    # Cancelamento + Observações no Bling). Import tardio: o serviço lê os
    # gatilhos de atenção DESTE módulo (fonte única) — em cima criaria ciclo.
    # Best-effort: falha do hold não pode derrubar o refresh do botão.
    auto_hold: dict | None = None
    try:
        from app.services.margem_auto_hold import run as _margem_auto_hold

        auto_hold = await _margem_auto_hold(session)
    except Exception as e:  # noqa: BLE001
        logger.warning("margem_auto_hold_refresh_failed", error=str(e)[:200])
    return {"refreshed": True, "rows": inserted, "auto_hold": auto_hold}


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
        await _patch_verificar_margem_status_silent(
            session,
            pedido_bling=pedido_bling,
            status=new_status,
        )

    await session.commit()
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
    # Pedido segurado pelo auto-hold da Margem (83955 + pino 'Pendente'):
    # Aprovar TEM de soltar no Bling mesmo quando o chamador pediu local-only.
    # A UI manda local_only para pendências de saldo/frete e o sync-saldo-final
    # auto-aprova com update_bling=False — sem esta exceção, a venda ficaria
    # presa em Aguardando Cancelamento no Bling com status Aprovado (e fora de
    # todas as abas). O hold de estoque (status NULL) e o reprovado no clique
    # (status='Reprovado') NÃO entram aqui.
    if (
        new_status == "Aprovado"
        and str(current_situacao_id or "") == str(SITUACAO_REPROVADO)
        and (order.status or "") == "Pendente"
    ):
        patch_bling = True

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
        await record_margem_audit(
            session,
            acao="situacao",
            pedido_bling=str(pedido_bling),
            bling_id=order.bling_id,
            sku=sku,
            valor_antigo=current_situacao_id,
            valor_novo=situacao_id,
            origem="margens",
            mudado_por=actor_id,
        )
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
    await _patch_verificar_margem_status_silent(
        session,
        pedido_bling=str(pedido_bling),
        status=new_status,
        aprovado_por=str(actor_id),
        verificado=True,
        # Espelha a situação no snapshot na hora (simétrico ao UPDATE de
        # bling_orders acima). Sem isso, Aprovar um pedido auto-segurado
        # (83955) deixaria o snapshot defasado e a linha sumiria de todas as
        # abas até o próximo rebuild.
        situacao=str(situacao_id) if patch_bling else None,
    )


async def _mark_pedido_pendente_by_pedido(
    session: AsyncSession,
    *,
    pedido_bling: int | str | None,
    sku: str | None,
) -> None:
    """Marca o pedido como Pendente no DaVinci, sem tocar na situação do Bling.

    Usado quando a edição do Saldo Efetivo NÃO aprova automaticamente porque a
    margem recalculada ficou abaixo da mínima: o pedido fica/permanece na aba
    Pendente para decisão manual. Ao contrário de `_apply_bling_decision_by_pedido`
    (que força Aprovado/Reprovado, atualiza a situação no Bling e marca
    verificado=True), aqui só gravamos status='Pendente', verificado=False e
    limpamos aprovado_por — em bling_orders e no snapshot.

    `bling_status_margem='Pendente'` é respeitado pela derivação de status e pelo
    filtro do endpoint de listagem, fixando o pedido na aba Pendente mesmo que
    ele não dispare mais nenhum gatilho de atenção (ex.: divergência de saldo
    resolvida pela própria edição).
    """
    if pedido_bling is None:
        raise HTTPException(400, detail={"code": "pedido_bling_missing"})

    order = await _find_bling_order_by_pedido(session, str(pedido_bling), sku)
    if order is None or order.bling_id is None:
        raise HTTPException(404, detail={"code": "bling_order_not_found"})

    await session.execute(
        update(BlingOrder)
        .where(BlingOrder.bling_id == order.bling_id)
        .values(status="Pendente", verificado=False, aprovado_por=None)
    )
    # Snapshot é best-effort (igual ao patch de status na aprovação): bling_orders
    # é a fonte que sobrevive ao rebuild do snapshot.
    try:
        await session.execute(
            text(
                f"""
                UPDATE {_VERIFICAR_MARGEM_TABLE}
                SET bling_status_margem = 'Pendente',
                    verificado = false,
                    aprovado_por = NULL
                WHERE pedido_bling = :pedido_bling
                """  # noqa: S608
            ),
            {"pedido_bling": str(pedido_bling)},
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "verificar_margem_pendente_patch_failed",
            pedido_bling=str(pedido_bling),
            error=str(e)[:200],
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
