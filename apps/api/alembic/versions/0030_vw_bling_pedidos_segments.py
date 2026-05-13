"""add segmento/subtype/min_margin columns to davinci.vw_bling_pedidos

Joins bling_orders.item_codigo → products.sku (case-insensitive, exact) →
products.segment_id → segments leaf + root. Exposes:

  * subtype_id     uuid  — segments.id of the leaf (subtype the product points to)
  * subtype        text  — leaf segment name (e.g. "Robusto", "12\"")
  * segmento       text  — root segment name (e.g. "Celular", "Mala")
  * min_margin     numeric(6,4) — leaf's `min_margin` floor (fraction; 0.15 = 15%)

Composite SKUs from kits (item_codigo with "+") match products by their full
string when the products table holds the same composite, otherwise rows fall
back to NULL.

Margin diff vs. floor is computed on the fly here too so callers can flag
under-margin rows without re-joining:

  * margin_floor_diff  = margem - min_margin

Revision ID: 0030_vw_bling_pedidos_segments
Revises: 0029_segments_min_margin
Create Date: 2026-05-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0030_vw_bling_pedidos_segments"
down_revision: str | None = "0029_segments_min_margin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


# Preserved from 0029_state: capturing the original definition here so the
# downgrade can restore it without depending on pg_get_viewdef from a future
# state.
ORIGINAL_VIEW_SQL = f"""
CREATE OR REPLACE VIEW {SCHEMA}.vw_bling_pedidos AS
 WITH order_totals AS (
         SELECT bo.numero,
            count(*) AS total_items,
            sum(COALESCE(bo.itemvalor, 0::numeric)) AS total_itemvalor_pedido,
            max(COALESCE(NULLIF(bo.valorbase, 0::numeric), bo.total, 0::numeric)) AS total_valorbase_pedido
           FROM {SCHEMA}.bling_orders bo
          WHERE (bo.item_index > 0 OR bo.item_index = 0 AND bo.itemvalor IS NOT NULL) AND (COALESCE(bo.valorbase, 0::numeric) > 0::numeric OR COALESCE(bo.total, 0::numeric) > 0::numeric)
          GROUP BY bo.numero
        ), proportional_values AS (
         SELECT bo.id, bo.numero, bo.numeroloja, bo.data, bo.totalprodutos, bo.total,
                bo.situacao, bo.loja, bo.store_id, bo.itens, bo.valorbase, bo.custofrete,
                bo.taxacomissao, bo.preco_custo, bo.item_id, bo.itemvalor, bo.item_codigo,
                bo.item_produto_id, bo.item_descricao, bo.item_quantidade, bo.item_desconto,
                bo.item_comissao_base, bo.item_comissao_valor, bo.created_at, bo.updated_at,
                bo.bling_id, bo.item_index, bo.categoria_id, bo.categoria_nome, bo."check",
                COALESCE(NULLIF(bo.valorbase, 0::numeric), bo.total, 0::numeric) AS valorbase_eff,
                COALESCE(ot.total_itemvalor_pedido, 0::numeric) AS total_itemvalor_pedido,
                COALESCE(ot.total_items, 1::bigint) AS total_items,
                COALESCE(ot.total_valorbase_pedido, 0::numeric) AS total_valorbase_pedido,
                CASE
                    WHEN COALESCE(ot.total_items, 1::bigint) = 1 THEN 1.0
                    WHEN ot.total_itemvalor_pedido > 0::numeric AND bo.itemvalor IS NOT NULL
                        THEN bo.itemvalor / ot.total_itemvalor_pedido
                    ELSE 1.0 / COALESCE(ot.total_items, 1::bigint)::numeric
                END AS item_proportion,
                CASE
                    WHEN COALESCE(ot.total_items, 1::bigint) = 1 THEN bo.total
                    WHEN ot.total_itemvalor_pedido > 0::numeric AND bo.itemvalor IS NOT NULL
                        THEN bo.total * (bo.itemvalor / ot.total_itemvalor_pedido)
                    ELSE bo.total / COALESCE(ot.total_items, 1::bigint)::numeric
                END AS total_proporcional,
                CASE
                    WHEN COALESCE(ot.total_items, 1::bigint) = 1
                        THEN COALESCE(NULLIF(bo.valorbase, 0::numeric), bo.total, 0::numeric)
                    WHEN ot.total_itemvalor_pedido > 0::numeric AND bo.itemvalor IS NOT NULL
                        THEN COALESCE(NULLIF(bo.valorbase, 0::numeric), bo.total, 0::numeric) * (bo.itemvalor / ot.total_itemvalor_pedido)
                    ELSE COALESCE(NULLIF(bo.valorbase, 0::numeric), bo.total, 0::numeric) / COALESCE(ot.total_items, 1::bigint)::numeric
                END AS valorbase_proporcional,
                CASE
                    WHEN COALESCE(ot.total_items, 1::bigint) = 1 THEN bo.custofrete
                    WHEN ot.total_itemvalor_pedido > 0::numeric AND bo.itemvalor IS NOT NULL
                        THEN bo.custofrete * (bo.itemvalor / ot.total_itemvalor_pedido)
                    ELSE bo.custofrete / COALESCE(ot.total_items, 1::bigint)::numeric
                END AS custofrete_proporcional,
                CASE
                    WHEN COALESCE(ot.total_items, 1::bigint) = 1 THEN bo.taxacomissao
                    WHEN ot.total_itemvalor_pedido > 0::numeric AND bo.itemvalor IS NOT NULL
                        THEN bo.taxacomissao * (bo.itemvalor / ot.total_itemvalor_pedido)
                    ELSE bo.taxacomissao / COALESCE(ot.total_items, 1::bigint)::numeric
                END AS taxacomissao_proporcional
           FROM {SCHEMA}.bling_orders bo
             LEFT JOIN order_totals ot ON bo.numero = ot.numero
          WHERE COALESCE(bo.valorbase, 0::numeric) > 0::numeric OR COALESCE(bo.total, 0::numeric) > 0::numeric
        )
 SELECT pv.id, pv.numero, pv.numeroloja, pv.data, pv.totalprodutos,
        pv.total_proporcional AS total, pv.situacao, sb.nome AS situacao_nome,
        pv.loja, s.apelido_override AS loja_nome, s.marketplace::text AS marketplace,
        pv.itens, pv.valorbase_proporcional AS valorbase,
        pv.custofrete_proporcional AS custofrete,
        pv.taxacomissao_proporcional AS taxacomissao,
        pv.item_id, pv.itemvalor, pv.item_codigo, pv.item_produto_id,
        pv.item_descricao, pv.item_quantidade, pv.item_desconto,
        pv.item_comissao_base, pv.item_comissao_valor,
        pv.created_at AS bo_created_at, pv.updated_at AS bo_updated_at,
        pv.bling_id, pv.item_proportion, pv.total_itemvalor_pedido,
        pv.valorbase AS original_valorbase, pv.custofrete AS original_custofrete,
        pv.taxacomissao AS original_taxacomissao,
        pv.preco_custo::numeric(10,2) AS preco_custo,
        pv.categoria_id, pv.categoria_nome, pv."check",
        CASE
            WHEN COALESCE(pv.preco_custo::numeric(10,2), 0::numeric(10,2)) > 0::numeric
                 AND COALESCE(pv.item_quantidade, 0) > 0
                 AND pv.valorbase_proporcional > 0::numeric
            THEN (pv.valorbase_proporcional
                  - (pv.custofrete_proporcional + pv.taxacomissao_proporcional)
                  - pv.preco_custo::numeric(10,2) * pv.item_quantidade::numeric)
                 / (pv.preco_custo::numeric(10,2) * pv.item_quantidade::numeric)
            ELSE NULL::numeric
        END AS margem
   FROM proportional_values pv
     LEFT JOIN {SCHEMA}.stores s ON s.id = pv.store_id
     LEFT JOIN {SCHEMA}.situacao_bling sb ON sb.id::text = pv.situacao;
"""


NEW_VIEW_SQL = f"""
CREATE OR REPLACE VIEW {SCHEMA}.vw_bling_pedidos AS
 WITH order_totals AS (
         SELECT bo.numero,
            count(*) AS total_items,
            sum(COALESCE(bo.itemvalor, 0::numeric)) AS total_itemvalor_pedido,
            max(COALESCE(NULLIF(bo.valorbase, 0::numeric), bo.total, 0::numeric)) AS total_valorbase_pedido
           FROM {SCHEMA}.bling_orders bo
          WHERE (bo.item_index > 0 OR bo.item_index = 0 AND bo.itemvalor IS NOT NULL) AND (COALESCE(bo.valorbase, 0::numeric) > 0::numeric OR COALESCE(bo.total, 0::numeric) > 0::numeric)
          GROUP BY bo.numero
        ), proportional_values AS (
         SELECT bo.id, bo.numero, bo.numeroloja, bo.data, bo.totalprodutos, bo.total,
                bo.situacao, bo.loja, bo.store_id, bo.itens, bo.valorbase, bo.custofrete,
                bo.taxacomissao, bo.preco_custo, bo.item_id, bo.itemvalor, bo.item_codigo,
                bo.item_produto_id, bo.item_descricao, bo.item_quantidade, bo.item_desconto,
                bo.item_comissao_base, bo.item_comissao_valor, bo.created_at, bo.updated_at,
                bo.bling_id, bo.item_index, bo.categoria_id, bo.categoria_nome, bo."check",
                COALESCE(NULLIF(bo.valorbase, 0::numeric), bo.total, 0::numeric) AS valorbase_eff,
                COALESCE(ot.total_itemvalor_pedido, 0::numeric) AS total_itemvalor_pedido,
                COALESCE(ot.total_items, 1::bigint) AS total_items,
                COALESCE(ot.total_valorbase_pedido, 0::numeric) AS total_valorbase_pedido,
                CASE
                    WHEN COALESCE(ot.total_items, 1::bigint) = 1 THEN 1.0
                    WHEN ot.total_itemvalor_pedido > 0::numeric AND bo.itemvalor IS NOT NULL
                        THEN bo.itemvalor / ot.total_itemvalor_pedido
                    ELSE 1.0 / COALESCE(ot.total_items, 1::bigint)::numeric
                END AS item_proportion,
                CASE
                    WHEN COALESCE(ot.total_items, 1::bigint) = 1 THEN bo.total
                    WHEN ot.total_itemvalor_pedido > 0::numeric AND bo.itemvalor IS NOT NULL
                        THEN bo.total * (bo.itemvalor / ot.total_itemvalor_pedido)
                    ELSE bo.total / COALESCE(ot.total_items, 1::bigint)::numeric
                END AS total_proporcional,
                CASE
                    WHEN COALESCE(ot.total_items, 1::bigint) = 1
                        THEN COALESCE(NULLIF(bo.valorbase, 0::numeric), bo.total, 0::numeric)
                    WHEN ot.total_itemvalor_pedido > 0::numeric AND bo.itemvalor IS NOT NULL
                        THEN COALESCE(NULLIF(bo.valorbase, 0::numeric), bo.total, 0::numeric) * (bo.itemvalor / ot.total_itemvalor_pedido)
                    ELSE COALESCE(NULLIF(bo.valorbase, 0::numeric), bo.total, 0::numeric) / COALESCE(ot.total_items, 1::bigint)::numeric
                END AS valorbase_proporcional,
                CASE
                    WHEN COALESCE(ot.total_items, 1::bigint) = 1 THEN bo.custofrete
                    WHEN ot.total_itemvalor_pedido > 0::numeric AND bo.itemvalor IS NOT NULL
                        THEN bo.custofrete * (bo.itemvalor / ot.total_itemvalor_pedido)
                    ELSE bo.custofrete / COALESCE(ot.total_items, 1::bigint)::numeric
                END AS custofrete_proporcional,
                CASE
                    WHEN COALESCE(ot.total_items, 1::bigint) = 1 THEN bo.taxacomissao
                    WHEN ot.total_itemvalor_pedido > 0::numeric AND bo.itemvalor IS NOT NULL
                        THEN bo.taxacomissao * (bo.itemvalor / ot.total_itemvalor_pedido)
                    ELSE bo.taxacomissao / COALESCE(ot.total_items, 1::bigint)::numeric
                END AS taxacomissao_proporcional
           FROM {SCHEMA}.bling_orders bo
             LEFT JOIN order_totals ot ON bo.numero = ot.numero
          WHERE COALESCE(bo.valorbase, 0::numeric) > 0::numeric OR COALESCE(bo.total, 0::numeric) > 0::numeric
        ), with_margin AS (
         SELECT pv.*,
                CASE
                    WHEN COALESCE(pv.preco_custo::numeric(10,2), 0::numeric(10,2)) > 0::numeric
                         AND COALESCE(pv.item_quantidade, 0) > 0
                         AND pv.valorbase_proporcional > 0::numeric
                    THEN (pv.valorbase_proporcional
                          - (pv.custofrete_proporcional + pv.taxacomissao_proporcional)
                          - pv.preco_custo::numeric(10,2) * pv.item_quantidade::numeric)
                         / (pv.preco_custo::numeric(10,2) * pv.item_quantidade::numeric)
                    ELSE NULL::numeric
                END AS margem
           FROM proportional_values pv
        )
 SELECT wm.id, wm.numero, wm.numeroloja, wm.data, wm.totalprodutos,
        wm.total_proporcional AS total, wm.situacao, sb.nome AS situacao_nome,
        wm.loja, s.apelido_override AS loja_nome, s.marketplace::text AS marketplace,
        wm.itens, wm.valorbase_proporcional AS valorbase,
        wm.custofrete_proporcional AS custofrete,
        wm.taxacomissao_proporcional AS taxacomissao,
        wm.item_id, wm.itemvalor, wm.item_codigo, wm.item_produto_id,
        wm.item_descricao, wm.item_quantidade, wm.item_desconto,
        wm.item_comissao_base, wm.item_comissao_valor,
        wm.created_at AS bo_created_at, wm.updated_at AS bo_updated_at,
        wm.bling_id, wm.item_proportion, wm.total_itemvalor_pedido,
        wm.valorbase AS original_valorbase, wm.custofrete AS original_custofrete,
        wm.taxacomissao AS original_taxacomissao,
        wm.preco_custo::numeric(10,2) AS preco_custo,
        wm.categoria_id, wm.categoria_nome, wm."check",
        wm.margem,
        -- segmento taxonomy joined via products.sku
        p.segment_id     AS subtype_id,
        leaf.name        AS subtype,
        root.name        AS segmento,
        leaf.min_margin  AS min_margin,
        CASE
            WHEN wm.margem IS NULL OR leaf.min_margin IS NULL THEN NULL::numeric
            ELSE wm.margem - leaf.min_margin
        END AS margin_floor_diff
   FROM with_margin wm
     LEFT JOIN {SCHEMA}.stores s ON s.id = wm.store_id
     LEFT JOIN {SCHEMA}.situacao_bling sb ON sb.id::text = wm.situacao
     LEFT JOIN {SCHEMA}.products p ON LOWER(p.sku) = LOWER(wm.item_codigo)
     LEFT JOIN {SCHEMA}.segments leaf ON leaf.id = p.segment_id
     LEFT JOIN {SCHEMA}.segments root ON root.id = leaf.parent_id;
"""


def upgrade() -> None:
    # CREATE OR REPLACE VIEW refuses to drop/rename existing columns. Drop and
    # recreate so the new columns (subtype_id, subtype, segmento, min_margin,
    # margin_floor_diff) can be appended.
    op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.vw_bling_pedidos CASCADE")
    op.execute(NEW_VIEW_SQL)


def downgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.vw_bling_pedidos CASCADE")
    op.execute(ORIGINAL_VIEW_SQL)
