# ruff: noqa: E501, S608
"""vw_bling_pedidos: piso de margem minima default 9% quando SKU nao casa no pricing

Regra de negocio (2026-06-02): pedidos cujo SKU nao casa nenhuma linha de
`pricing_products` ("sem cadastro" na pagina Margem) ficavam com
`min_margin = NULL` (nao havia leaf de segmento), logo sem piso de margem
e sem `margin_floor_diff`. O usuario decidiu que esses casos devem usar
uma **margem minima default de 9% (0.09)**.

Correcao: em `vw_bling_pedidos`, trocar `leaf.min_margin` por
`COALESCE(leaf.min_margin, 0.09)` no campo exposto `min_margin` e no
calculo de `margin_floor_diff`. Cast para numeric(6,4) no `min_margin`
para preservar o tipo da coluna (CREATE OR REPLACE exige tipo identico).
O `margin_floor_diff` passa a ser calculado sempre que `wm.margem` nao for
nulo (antes era NULL tambem quando faltava o leaf).

Escala confirmada: min_margin e fracao (Apple=0.07, Diversos=0.08,
Regular/Robusto=0.14). 9% = 0.09.

Aplica-se via CREATE OR REPLACE (colunas/tipos de saida inalterados), entao
as views dependentes (vw_conciliacao_margens_marketplace[_all]) refletem a
mudanca automaticamente — `margem_minima`, `bling_margem_vs_minima` e
`marketplace_margem_vs_minima` herdam o piso de 9% via `bp.min_margin`.
O snapshot davinci.verificar_margem e repopulado (mesma logica do
rebuild_all). Idempotente: se a view ja contem o COALESCE, o replace e
pulado (mas o snapshot ainda e repopulado).

Revision ID: 0125_vw_bling_pedidos_min_margin_floor_default
Revises: 0124_seed_celular_cotacao_values
Create Date: 2026-06-02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0125_vw_bling_pedidos_min_margin_floor_default"
down_revision: str | None = "0124_seed_celular_cotacao_values"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
VIEW_NAME = "vw_bling_pedidos"
SNAPSHOT = "verificar_margem"
VIEW_CONCILIACAO = "vw_conciliacao_margens_marketplace"

# Marker que indica que a migration ja foi aplicada (idempotencia).
_MARKER = "COALESCE(leaf.min_margin, 0.09)::numeric(6,4) AS min_margin,"

# Campo exposto min_margin (linha do SELECT). Cast preserva numeric(6,4).
_SEL_OLD = "leaf.min_margin,"
_SEL_NEW = "COALESCE(leaf.min_margin, 0.09)::numeric(6,4) AS min_margin,"

# Guard do margin_floor_diff: deixa de exigir leaf.min_margin nao nulo.
_WHEN_OLD = "WHEN wm.margem IS NULL OR leaf.min_margin IS NULL THEN NULL::numeric"
_WHEN_NEW = "WHEN wm.margem IS NULL THEN NULL::numeric"

# Calculo do margin_floor_diff usa o piso default tambem.
_ELSE_OLD = "ELSE wm.margem - leaf.min_margin"
_ELSE_NEW = "ELSE wm.margem - COALESCE(leaf.min_margin, 0.09)"


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
    """Repopula verificar_margem (mesma logica do service.rebuild_all)."""
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
    # ON CONFLICT DO NOTHING: tolera corrida com writers concorrentes
    # (app/worker fazem refresh_for_pedido por evento). Sem isso, uma linha
    # inserida pelo app entre o DELETE e o INSERT estoura o PK e aborta a
    # migration. O rebuild_all original so nao sofre disso por rodar as 5h
    # sem trafego.
    op.execute(
        f"INSERT INTO {snap} SELECT * FROM {view} "
        "ON CONFLICT (bling_order_item_id) DO NOTHING"
    )


def upgrade() -> None:
    sql = _current_view_sql()
    if _MARKER not in sql:
        sql = _replace_exact(sql, _SEL_OLD, _SEL_NEW, 1)
        sql = _replace_exact(sql, _WHEN_OLD, _WHEN_NEW, 1)
        sql = _replace_exact(sql, _ELSE_OLD, _ELSE_NEW, 1)
        _create_or_replace(sql)
    _refresh_snapshot()


def downgrade() -> None:
    sql = _current_view_sql()
    if _MARKER in sql:
        sql = _replace_exact(sql, _SEL_NEW, _SEL_OLD, 1)
        sql = _replace_exact(sql, _WHEN_NEW, _WHEN_OLD, 1)
        sql = _replace_exact(sql, _ELSE_NEW, _ELSE_OLD, 1)
        _create_or_replace(sql)
    _refresh_snapshot()
