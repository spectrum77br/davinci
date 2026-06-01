# ruff: noqa: E501, S608
"""vw_bling_pedidos: rateio proporcional ponderado por quantidade

Bug: o rateio do pedido entre as linhas (item_proportion e os
*_proporcional) usava `bo.itemvalor / SUM(bo.itemvalor)`, onde
`itemvalor` e o valor UNITARIO do item. Em pedidos multi-linha onde o
mesmo (ou parecido) valor unitario aparece com quantidades diferentes,
isso divide a receita do pedido em partes IGUAIS por linha, ignorando a
quantidade — enquanto o custo (`preco_custo * item_quantidade`) escala
com a quantidade. O descompasso gera margens divergentes entre linhas do
mesmo produto (ex.: pedido 278654 -> -24.5% / 13.3% / 126.6%, quando o
correto e 13.3% em todas).

Correcao: ponderar o rateio pela RECEITA da linha = itemvalor *
item_quantidade, tanto no total do pedido (order_totals) quanto nos
numeradores das proporcoes (proportional_values). Apos isso cada linha
recebe receita proporcional a sua quantidade e a margem fica consistente.

Aplica-se via CREATE OR REPLACE (colunas de saida inalteradas), entao as
views dependentes (vw_conciliacao_margens_marketplace[_all]) refletem a
correcao automaticamente. O snapshot davinci.verificar_margem e
repopulado (mesma logica do rebuild_all: delete na janela da view base +
delete de orfaos + reinsert), preservando linhas antigas fora da janela.

Revision ID: 0113_vw_bling_pedidos_qty_weighted_proportion
Revises: 0112_vw_devolucoes_manutencao
Create Date: 2026-06-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0113_vw_bling_pedidos_qty_weighted_proportion"
down_revision: str | None = "0112_vw_devolucoes_manutencao"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
VIEW_NAME = "vw_bling_pedidos"
SNAPSHOT = "verificar_margem"
VIEW_CONCILIACAO = "vw_conciliacao_margens_marketplace"

# order_totals: o total do pedido tambem precisa ponderar por quantidade,
# senao a soma das proporcoes nao fecha em 1.
_TOTAL_OLD = "sum(COALESCE(bo.itemvalor, 0::numeric)) AS total_itemvalor_pedido"
_TOTAL_NEW = (
    "sum(COALESCE(bo.itemvalor, 0::numeric) * COALESCE(bo.item_quantidade, 1)::numeric) "
    "AS total_itemvalor_pedido"
)

# Numerador das proporcoes (aparece 5x: item_proportion, total_proporcional,
# valorbase_proporcional, custofrete_proporcional, taxacomissao_proporcional).
_PROP_OLD = "bo.itemvalor / ot.total_itemvalor_pedido"
_PROP_NEW = "bo.itemvalor * COALESCE(bo.item_quantidade, 1)::numeric / ot.total_itemvalor_pedido"
_PROP_COUNT = 5


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
    op.execute(f"INSERT INTO {snap} SELECT * FROM {view}")


def upgrade() -> None:
    sql = _current_view_sql()
    sql = _replace_exact(sql, _TOTAL_OLD, _TOTAL_NEW, 1)
    sql = _replace_exact(sql, _PROP_OLD, _PROP_NEW, _PROP_COUNT)
    _create_or_replace(sql)
    _refresh_snapshot()


def downgrade() -> None:
    sql = _current_view_sql()
    sql = _replace_exact(sql, _TOTAL_NEW, _TOTAL_OLD, 1)
    sql = _replace_exact(sql, _PROP_NEW, _PROP_OLD, _PROP_COUNT)
    _create_or_replace(sql)
    _refresh_snapshot()
