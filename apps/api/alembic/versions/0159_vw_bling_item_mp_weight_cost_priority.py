# ruff: noqa: E501, S608
"""vw_bling_item_mp_weight: rateio do líquido por CUSTO do item (todos os mkts)

Bug (relatado 2026-06-25, pedido 283424): o rateio do líquido do pedido entre as
linhas (`item_proportion` e os `*_proporcional`) pondera pelo PREÇO de venda do
item — preço real do escrow Shopee (migration 0154) ou `itemvalor` do Bling. Em
combos com um brinde/acessório vendido por ~R$ 0 (ex.: 283424, fone a001.ci a
R$ 0,78 junto de um celular z0208), o item barato recebe quase nada da receita e
aparece com prejuízo absurdo (−83%), enquanto o item caro absorve tudo. O custo
NÃO é proporcional ao preço, então a margem por linha sai distorcida.

Correção (decisão do dono, 2026-06-25 — "ratear pelo custo"): quando o pedido tem
cobertura TOTAL de custo (todo item com `preco_custo` > 0), o peso de rateio passa
a ser o CUSTO do item (`preco_custo * quantidade`). Aí o líquido do pedido é
dividido na proporção do custo e CADA SKU mostra a margem real do pedido sobre o
custo (em 283424: ambos 21,2%, batendo com a planilha do usuário; em 282986: 43,3%
uniforme). Vale pra TODOS os marketplaces (Shopee/ML/Amazon), porque `item_mp`
varre todas as `bling_orders`.

Fallback preservado (por pedido, pra não misturar bases de escala): se ALGUM item
do pedido não tem custo cadastrado (`preco_custo` NULL/0), o pedido inteiro mantém
exatamente o comportamento anterior — preço real Shopee se houver cobertura total,
senão `itemvalor`. Isso também evita divisão por zero (sem custo total → cai no
peso antigo, garantidamente > 0 quando valorbase/total > 0).

Pedidos de 1 item não mudam (`item_proportion` = 1.0 por construção em
vw_bling_pedidos). Só a coluna de saída `eff_weight` muda de valor; as colunas/tipos
da view são idênticas, então o CREATE OR REPLACE é aceito e as consumidoras
(vw_bling_pedidos → vw_conciliacao_margens_marketplace[_all] →
conciliacao_margens_for_bling_id) refletem a correção automaticamente por referência
de NOME. O snapshot davinci.verificar_margem é repopulado dentro da janela (mesma
lógica do rebuild_all).

Revision ID: 0159_vw_bling_item_mp_weight_cost_priority
Revises: 0158_bling_envio_corte_10h
Create Date: 2026-06-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0159_vw_bling_item_mp_weight_cost_priority"
down_revision: str | None = "0158_bling_envio_corte_10h"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
HELPER_VIEW = "vw_bling_item_mp_weight"
SNAPSHOT = "verificar_margem"
VIEW_CONCILIACAO = "vw_conciliacao_margens_marketplace"

# Cabeça comum às duas versões (Shopee escrow + item_mp). A diferença está só no
# item_mp (cost_weight), no cov (cost_items/total_cost) e no CASE do eff_weight.
_FIN_MP = f"""
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
"""

# NEW: peso por custo, com cobertura total por pedido decidindo a base.
_HELPER_SQL_NEW = f"""
CREATE OR REPLACE VIEW {SCHEMA}.{HELPER_VIEW} AS{_FIN_MP}
item_mp AS (
  SELECT bo.id,
         bo.numero,
         COALESCE(bo.itemvalor, 0::numeric) * COALESCE(bo.item_quantidade, 1)::numeric AS bling_weight,
         -- preco_custo é double precision (resto é numeric). Cast explícito p/ o
         -- eff_weight permanecer numeric — senão o CASE vira double e o
         -- CREATE OR REPLACE falha ("cannot change data type of view column").
         COALESCE(bo.preco_custo::numeric, 0::numeric) * COALESCE(bo.item_quantidade, 1)::numeric AS cost_weight,
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
         sum(COALESCE(mp_line_value, 0::numeric)) AS total_mp,
         count(*) FILTER (WHERE cost_weight > 0::numeric) AS cost_items,
         sum(cost_weight) AS total_cost
  FROM item_mp
  GROUP BY numero
)
SELECT im.id AS bling_order_item_id,
       im.numero,
       CASE
           WHEN c.cost_items = c.total_items AND c.total_cost > 0::numeric
           THEN im.cost_weight
           WHEN c.matched_items = c.total_items AND c.total_mp > 0::numeric
           THEN im.mp_line_value
           ELSE im.bling_weight
       END AS eff_weight
FROM item_mp im
JOIN cov c ON c.numero = im.numero;
"""

# OLD: versão da migration 0154 (peso por preço real Shopee, fallback itemvalor).
_HELPER_SQL_OLD = f"""
CREATE OR REPLACE VIEW {SCHEMA}.{HELPER_VIEW} AS{_FIN_MP}
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
    op.execute(_HELPER_SQL_NEW)
    _refresh_snapshot()


def downgrade() -> None:
    op.execute(f"SET LOCAL search_path TO {SCHEMA}, public")
    op.execute(_HELPER_SQL_OLD)
    _refresh_snapshot()
