# ruff: noqa: E501, S608
"""vw_bling_pedidos: rateio por preço REAL do item no marketplace (Shopee)

Bug (relatado 2026-06-23, pedido 282986): o rateio do líquido do pedido entre
as linhas (item_proportion e os *_proporcional) pondera por `bo.itemvalor` — o
valor do item no Bling. Em pedidos multi-item da Shopee esse valor costuma vir
ACHATADO (todos os itens ~R$ 500), mesmo quando a Shopee vendeu cada item por um
preço bem diferente. Como o custo varia por item, a margem por linha sai
distorcida: itens caros (custo alto) recebem a mesma fatia de receita que itens
baratos e aparecem com margem irrisória (ex.: 282986 -> b100/b101 a 7%, quando o
correto é ~39%).

O preço real por item EXISTE: vem do escrow da Shopee em
`marketplace_order_financials.raw -> escrow.order_income.items[]`
(`model_sku` + `discounted_price`). Casando `model_sku` = `item_codigo`, o rateio
passa a refletir o que o cliente pagou por cada item.

Correção:
  - Nova view auxiliar `vw_bling_item_mp_weight`: para cada linha de
    `bling_orders` resolve um "peso efetivo" (`eff_weight`). Quando o pedido tem
    cobertura TOTAL de preços do marketplace (todos os itens casaram com o escrow
    Shopee e a soma > 0) usa o `discounted_price * quantity` real; senão cai no
    fallback histórico `itemvalor * quantidade` (decisão por pedido, para não
    misturar bases de escala diferente dentro do mesmo pedido).
  - `vw_bling_pedidos`: o numerador das proporções (5x) e o total do pedido
    (`order_totals`) passam a usar `eff_weight` (com COALESCE para o peso Bling
    quando a auxiliar não cobre a linha). Demais colunas/tipos inalterados.

Pedidos de 1 item não mudam (proporção = 1.0 por construção). Pedidos sem
cobertura Shopee (ML/Amazon/Shopee sem escrow) mantêm exatamente o rateio
anterior — validado: 0 linhas single-item e 0 linhas de fallback mudam; só
pedidos Shopee multi-item com cobertura total são afetados.

Aplica-se via CREATE OR REPLACE (colunas de saída inalteradas), então as views
dependentes (vw_conciliacao_margens_marketplace[_all]) e a função
conciliacao_margens_for_bling_id — que referenciam vw_bling_pedidos por NOME —
refletem a correção automaticamente. O snapshot davinci.verificar_margem é
repopulado dentro da janela (mesma lógica do rebuild_all). Idempotente: se a
view já contém o marcador, o replace é pulado (mas o snapshot ainda repopula).

Revision ID: 0154_vw_bling_pedidos_marketplace_item_weight
Revises: 0153_valuation_estoque_bling_diario
Create Date: 2026-06-23
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0154_vw_bling_pedidos_marketplace_item_weight"
down_revision: str | None = "0153_valuation_estoque_bling_diario"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
VIEW_NAME = "vw_bling_pedidos"
HELPER_VIEW = "vw_bling_item_mp_weight"
SNAPSHOT = "verificar_margem"
VIEW_CONCILIACAO = "vw_conciliacao_margens_marketplace"

# Marcador de idempotência: presente quando o rateio já usa o peso do marketplace.
_MARKER = HELPER_VIEW

# 1) order_totals: total do pedido soma o peso efetivo (fallback p/ itemvalor*qtd).
_TOTAL_OLD = "sum(COALESCE(bo.itemvalor, 0::numeric) * COALESCE(bo.item_quantidade, 1)::numeric) AS total_itemvalor_pedido"
_TOTAL_NEW = "sum(COALESCE(w.eff_weight, COALESCE(bo.itemvalor, 0::numeric) * COALESCE(bo.item_quantidade, 1)::numeric)) AS total_itemvalor_pedido"

# 2) order_totals: join na auxiliar.
_OT_FROM_OLD = "FROM bling_orders bo\n          WHERE (bo.item_index > 0"
_OT_FROM_NEW = (
    "FROM bling_orders bo\n"
    f"             LEFT JOIN {SCHEMA}.{HELPER_VIEW} w ON w.bling_order_item_id = bo.id\n"
    "          WHERE (bo.item_index > 0"
)

# 3) proportional_values: join na auxiliar.
_PV_FROM_OLD = "FROM bling_orders bo\n             LEFT JOIN order_totals ot ON bo.numero = ot.numero"
_PV_FROM_NEW = (
    "FROM bling_orders bo\n"
    f"             LEFT JOIN {SCHEMA}.{HELPER_VIEW} w ON w.bling_order_item_id = bo.id\n"
    "             LEFT JOIN order_totals ot ON bo.numero = ot.numero"
)

# 4) numerador das proporções (5x: item_proportion, total/valorbase/custofrete/taxacomissao).
_PROP_OLD = "bo.itemvalor * COALESCE(bo.item_quantidade, 1)::numeric / ot.total_itemvalor_pedido"
_PROP_NEW = "COALESCE(w.eff_weight, bo.itemvalor * COALESCE(bo.item_quantidade, 1)::numeric) / ot.total_itemvalor_pedido"
_PROP_COUNT = 5

_HELPER_SQL = f"""
CREATE OR REPLACE VIEW {SCHEMA}.{HELPER_VIEW} AS
WITH fin AS (
  SELECT DISTINCT ON (f.bling_id) f.bling_id, f.raw
  FROM {SCHEMA}.marketplace_order_financials f
  WHERE f.platform::text = 'shopee'
    AND jsonb_typeof(f.raw #> '{{escrow,order_income,items}}') = 'array'
  ORDER BY f.bling_id, f.fetched_at DESC NULLS LAST
),
mp_items AS (
  SELECT fin.bling_id,
         lower(elem ->> 'model_sku') AS sku,
         sum((elem ->> 'discounted_price')::numeric
             * COALESCE(NULLIF(elem ->> 'quantity_purchased', '')::numeric, 1)) AS mp_line_value
  FROM fin
  CROSS JOIN LATERAL jsonb_array_elements(fin.raw #> '{{escrow,order_income,items}}') elem
  WHERE elem ->> 'model_sku' IS NOT NULL
    AND (elem ->> 'discounted_price') IS NOT NULL
  GROUP BY fin.bling_id, lower(elem ->> 'model_sku')
),
item_mp AS (
  SELECT bo.id,
         bo.numero,
         COALESCE(bo.itemvalor, 0::numeric) * COALESCE(bo.item_quantidade, 1)::numeric AS bling_weight,
         mp.mp_line_value
  FROM {SCHEMA}.bling_orders bo
  LEFT JOIN mp_items mp ON mp.bling_id = bo.bling_id AND mp.sku = lower(bo.item_codigo)
  WHERE (bo.item_index > 0 OR (bo.item_index = 0 AND bo.itemvalor IS NOT NULL))
    AND (COALESCE(bo.valorbase, 0::numeric) > 0::numeric OR COALESCE(bo.total, 0::numeric) > 0::numeric)
),
cov AS (
  SELECT numero,
         count(*) AS total_items,
         count(mp_line_value) AS matched_items,
         sum(COALESCE(mp_line_value, 0::numeric)) AS total_mp
  FROM item_mp
  GROUP BY numero
)
SELECT im.id AS bling_order_item_id,
       im.numero,
       CASE
           WHEN c.matched_items = c.total_items AND c.total_mp > 0::numeric
           THEN im.mp_line_value
           ELSE im.bling_weight
       END AS eff_weight
FROM item_mp im
JOIN cov c ON c.numero = im.numero;
"""


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
    op.execute(f"SET LOCAL search_path TO {SCHEMA}, public")
    op.execute(_HELPER_SQL)
    sql = _current_view_sql()
    if _MARKER not in sql:
        sql = _replace_exact(sql, _TOTAL_OLD, _TOTAL_NEW, 1)
        sql = _replace_exact(sql, _OT_FROM_OLD, _OT_FROM_NEW, 1)
        sql = _replace_exact(sql, _PV_FROM_OLD, _PV_FROM_NEW, 1)
        sql = _replace_exact(sql, _PROP_OLD, _PROP_NEW, _PROP_COUNT)
        _create_or_replace(sql)
    _refresh_snapshot()


def downgrade() -> None:
    op.execute(f"SET LOCAL search_path TO {SCHEMA}, public")
    sql = _current_view_sql()
    if _MARKER in sql:
        sql = _replace_exact(sql, _PROP_NEW, _PROP_OLD, _PROP_COUNT)
        sql = _replace_exact(sql, _PV_FROM_NEW, _PV_FROM_OLD, 1)
        sql = _replace_exact(sql, _OT_FROM_NEW, _OT_FROM_OLD, 1)
        sql = _replace_exact(sql, _TOTAL_NEW, _TOTAL_OLD, 1)
        _create_or_replace(sql)
    op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.{HELPER_VIEW}")
    _refresh_snapshot()
