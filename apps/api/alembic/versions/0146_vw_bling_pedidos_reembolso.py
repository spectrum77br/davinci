# ruff: noqa: E501, S608
"""vw_bling_pedidos: soma reembolso (rateado por item) no lucro e na margem

Regra de negócio (2026-06-16): o reembolso lançado na página de Reembolso
(`davinci.refunds`, replicado em `bling_orders.reembolso` pela migration 0145)
entra nos cálculos de rentabilidade da view:

  - lucro  += reembolso * item_proportion
  - margem  = (lucro + reembolso * item_proportion) / (custo * qtd)
             (apenas no NUMERADOR — o denominador, base de custo, não muda)

O valor cheio do pedido fica em todas as linhas (`bling_orders.reembolso`); a
multiplicação por `item_proportion` rateia entre os itens, então a soma por
pedido bate exatamente o reembolso (proporções somam 1; pedido de 1 item ->
proporção 1.0). O reembolso já vem com sinal (positivo soma, negativo subtrai).

Limitação conhecida: lucro/margem só são calculados quando há custo (>0), qtd
(>0) e valorbase (>0); itens sem custo continuam NULL e não recebem a parcela
do reembolso. Consistente com o comportamento atual do lucro.

Aplica-se via CREATE OR REPLACE (colunas/tipos de saída inalterados — reembolso
NÃO é exposto como coluna nova, só entra em lucro/margem), então as views
dependentes (vw_conciliacao_margens_marketplace[_all]) refletem a mudança
automaticamente. O snapshot davinci.verificar_margem é repopulado (mesma lógica
do rebuild_all). Idempotente: se a view já contém o marcador, o replace é
pulado (mas o snapshot ainda é repopulado).

Revision ID: 0146_vw_bling_pedidos_reembolso
Revises: 0145_bling_orders_reembolso
Create Date: 2026-06-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0146_vw_bling_pedidos_reembolso"
down_revision: str | None = "0145_bling_orders_reembolso"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
VIEW_NAME = "vw_bling_pedidos"
SNAPSHOT = "verificar_margem"
VIEW_CONCILIACAO = "vw_conciliacao_margens_marketplace"

# Marcador de idempotência: presente quando a coluna reembolso já foi propagada.
_MARKER = "bo.reembolso,"

# 1) Carrega bo.reembolso na CTE proportional_values (após aprovado_por).
_PV_OLD = "            bo.aprovado_por,\n            COALESCE(NULLIF(bo.valorbase"
_PV_NEW = "            bo.aprovado_por,\n            bo.reembolso,\n            COALESCE(NULLIF(bo.valorbase"

# 2) Carrega pv.reembolso na CTE with_margin (após aprovado_por).
_WM_OLD = "            pv.aprovado_por,\n            pv.valorbase_eff,"
_WM_NEW = "            pv.aprovado_por,\n            pv.reembolso,\n            pv.valorbase_eff,"

# 3) Numerador da margem (em with_margin) recebe + reembolso rateado.
_MARGEM_OLD = "- pv.preco_custo::numeric(10,2) * pv.item_quantidade::numeric) / (pv.preco_custo::numeric(10,2) * pv.item_quantidade::numeric)"
_MARGEM_NEW = "- pv.preco_custo::numeric(10,2) * pv.item_quantidade::numeric + COALESCE(pv.reembolso, 0::double precision)::numeric * pv.item_proportion) / (pv.preco_custo::numeric(10,2) * pv.item_quantidade::numeric)"

# 4) Lucro (SELECT final) recebe + reembolso rateado.
_LUCRO_OLD = "- wm.preco_custo::numeric(10,2) * wm.item_quantidade::numeric\n            ELSE NULL::numeric\n        END AS lucro"
_LUCRO_NEW = "- wm.preco_custo::numeric(10,2) * wm.item_quantidade::numeric + COALESCE(wm.reembolso, 0::double precision)::numeric * wm.item_proportion\n            ELSE NULL::numeric\n        END AS lucro"


def _current_view_sql() -> str:
    bind = op.get_bind()
    sql = sa.text("SELECT pg_get_viewdef(CAST(:v AS regclass), true)")
    return bind.execute(sql, {"v": f"{SCHEMA}.{VIEW_NAME}"}).scalar_one()


def _replace_exact(sql: str, old: str, new: str, expected: int) -> str:
    found = sql.count(old)
    if found != expected:
        raise RuntimeError(
            f"vw_bling_pedidos rewrite: esperava {expected} ocorrencia(s) de "
            f"{old!r}, achei {found}"
        )
    return sql.replace(old, new)


def _create_or_replace(view_sql: str) -> None:
    op.execute(f"SET LOCAL search_path TO {SCHEMA}, public")
    op.execute(f"CREATE OR REPLACE VIEW {SCHEMA}.{VIEW_NAME} AS\n{view_sql}")


def _refresh_snapshot() -> None:
    """Repopula verificar_margem (mesma lógica do service.rebuild_all)."""
    snap = f'"{SCHEMA}"."{SNAPSHOT}"'
    view = f'"{SCHEMA}"."{VIEW_CONCILIACAO}"'
    bo = f'"{SCHEMA}"."bling_orders"'
    op.execute(
        f"DELETE FROM {snap} v WHERE v.bling_order_item_id IN "
        f"(SELECT bling_order_item_id FROM {view})"
    )
    op.execute(
        f"DELETE FROM {snap} v WHERE NOT EXISTS "
        f"(SELECT 1 FROM {bo} bo WHERE bo.id = v.bling_order_item_id)"
    )
    op.execute(
        f"INSERT INTO {snap} SELECT * FROM {view} "
        "ON CONFLICT (bling_order_item_id) DO NOTHING"
    )


def upgrade() -> None:
    sql = _current_view_sql()
    if _MARKER not in sql:
        sql = _replace_exact(sql, _PV_OLD, _PV_NEW, 1)
        sql = _replace_exact(sql, _WM_OLD, _WM_NEW, 1)
        sql = _replace_exact(sql, _MARGEM_OLD, _MARGEM_NEW, 1)
        sql = _replace_exact(sql, _LUCRO_OLD, _LUCRO_NEW, 1)
        _create_or_replace(sql)
    _refresh_snapshot()


def downgrade() -> None:
    sql = _current_view_sql()
    if _MARKER in sql:
        sql = _replace_exact(sql, _LUCRO_NEW, _LUCRO_OLD, 1)
        sql = _replace_exact(sql, _MARGEM_NEW, _MARGEM_OLD, 1)
        sql = _replace_exact(sql, _WM_NEW, _WM_OLD, 1)
        sql = _replace_exact(sql, _PV_NEW, _PV_OLD, 1)
        _create_or_replace(sql)
    _refresh_snapshot()
