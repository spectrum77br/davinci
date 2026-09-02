# ruff: noqa: E501, S608
"""vw_bling_pedidos: SKU 'z%' sem cadastro herda a minima da Queima de estoque

CONTEXTO (Eduardo, 02/09): pedidos com SKU comecando com "z" sao itens de
queima de estoque, quase nunca cadastrados na Precificacao. Sem cadastro a
minima caia no padrao de 9% e a Margem segurava tudo (ex.: -11,4% < 9%),
mas a regra do negocio pra queima aprova ate -15%.

FIX: o fallback da minima ganha um degrau no meio -- cadastro do produto
(leaf.min_margin) > SKU z% -> minima do segmento "Queima de estoque" (hoje
-15%; segue o cadastro se a minima do segmento for editada) > padrao 0.09.
Se o segmento sumir ou for renomeado, o degrau vira NULL e volta pro 9%
(conservador). Triagem, auto-hold e Informar leem margem_minima do snapshot
verificar_margem -> herdam no proximo refresh, sem mudanca de codigo.

Corpo copiado programaticamente da 0193 (CTEs/joins identicos); so mudam as
duas expressoes de min_margin/margin_floor_diff e o JOIN qz no final.

Revision ID: 0235_vw_bling_pedidos_z_sku_queima_minima
Revises: 0234_chamados
Create Date: 2026-09-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0235_vw_bling_pedidos_z_sku_queima_minima"
down_revision: str | None = "0234_chamados"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CTES = r"""
 WITH order_totals AS (
         SELECT bo.numero,
            count(*) AS total_items,
            sum(COALESCE(w.eff_weight, COALESCE(bo.itemvalor, 0::numeric) * COALESCE(bo.item_quantidade, 1)::numeric)) AS total_itemvalor_pedido,
            max(COALESCE(NULLIF(bo.valorbase, 0::numeric), bo.total, 0::numeric)) AS total_valorbase_pedido
           FROM bling_orders bo
             LEFT JOIN vw_bling_item_mp_weight w ON w.bling_order_item_id = bo.id
          WHERE (bo.item_index > 0 OR bo.item_index = 0 AND bo.itemvalor IS NOT NULL) AND (COALESCE(bo.valorbase, 0::numeric) > 0::numeric OR COALESCE(bo.total, 0::numeric) > 0::numeric)
          GROUP BY bo.numero
        ), proportional_values AS (
         SELECT bo.id,
            bo.numero,
            bo.numeroloja,
            bo.data,
            bo.totalprodutos,
            bo.total,
            bo.situacao,
            bo.loja,
            bo.store_id,
            bo.itens,
            bo.valorbase,
            bo.custofrete,
            bo.taxacomissao,
            bo.preco_custo,
            bo.item_id,
            bo.itemvalor,
            bo.item_codigo,
            bo.item_produto_id,
            bo.item_descricao,
            bo.item_quantidade,
            bo.item_desconto,
            bo.item_comissao_base,
            bo.item_comissao_valor,
            bo.created_at,
            bo.updated_at,
            bo.bling_id,
            bo.item_index,
            bo.categoria_id,
            bo.categoria_nome,
            bo.verificado,
            bo.status,
            bo.aprovado_por,
            bo.reembolso,
            COALESCE(NULLIF(bo.valorbase, 0::numeric), bo.total, 0::numeric) AS valorbase_eff,
            COALESCE(ot.total_itemvalor_pedido, 0::numeric) AS total_itemvalor_pedido,
            COALESCE(ot.total_items, 1::bigint) AS total_items,
            COALESCE(ot.total_valorbase_pedido, 0::numeric) AS total_valorbase_pedido,
                CASE
                    WHEN COALESCE(ot.total_items, 1::bigint) = 1 THEN 1.0
                    WHEN ot.total_itemvalor_pedido > 0::numeric AND bo.itemvalor IS NOT NULL THEN COALESCE(w.eff_weight, bo.itemvalor * COALESCE(bo.item_quantidade, 1)::numeric) / ot.total_itemvalor_pedido
                    ELSE 1.0 / COALESCE(ot.total_items, 1::bigint)::numeric
                END AS item_proportion,
                CASE
                    WHEN COALESCE(ot.total_items, 1::bigint) = 1 THEN bo.total
                    WHEN ot.total_itemvalor_pedido > 0::numeric AND bo.itemvalor IS NOT NULL THEN bo.total * (COALESCE(w.eff_weight, bo.itemvalor * COALESCE(bo.item_quantidade, 1)::numeric) / ot.total_itemvalor_pedido)
                    ELSE bo.total / COALESCE(ot.total_items, 1::bigint)::numeric
                END AS total_proporcional,
                CASE
                    WHEN COALESCE(ot.total_items, 1::bigint) = 1 THEN COALESCE(NULLIF(bo.valorbase, 0::numeric), bo.total, 0::numeric)
                    WHEN ot.total_itemvalor_pedido > 0::numeric AND bo.itemvalor IS NOT NULL THEN COALESCE(NULLIF(bo.valorbase, 0::numeric), bo.total, 0::numeric) * (COALESCE(w.eff_weight, bo.itemvalor * COALESCE(bo.item_quantidade, 1)::numeric) / ot.total_itemvalor_pedido)
                    ELSE COALESCE(NULLIF(bo.valorbase, 0::numeric), bo.total, 0::numeric) / COALESCE(ot.total_items, 1::bigint)::numeric
                END AS valorbase_proporcional,
                CASE
                    WHEN COALESCE(ot.total_items, 1::bigint) = 1 THEN bo.custofrete
                    WHEN ot.total_itemvalor_pedido > 0::numeric AND bo.itemvalor IS NOT NULL THEN bo.custofrete * (COALESCE(w.eff_weight, bo.itemvalor * COALESCE(bo.item_quantidade, 1)::numeric) / ot.total_itemvalor_pedido)
                    ELSE bo.custofrete / COALESCE(ot.total_items, 1::bigint)::numeric
                END AS custofrete_proporcional,
                CASE
                    WHEN COALESCE(ot.total_items, 1::bigint) = 1 THEN bo.taxacomissao
                    WHEN ot.total_itemvalor_pedido > 0::numeric AND bo.itemvalor IS NOT NULL THEN bo.taxacomissao * (COALESCE(w.eff_weight, bo.itemvalor * COALESCE(bo.item_quantidade, 1)::numeric) / ot.total_itemvalor_pedido)
                    ELSE bo.taxacomissao / COALESCE(ot.total_items, 1::bigint)::numeric
                END AS taxacomissao_proporcional
           FROM bling_orders bo
             LEFT JOIN vw_bling_item_mp_weight w ON w.bling_order_item_id = bo.id
             LEFT JOIN order_totals ot ON bo.numero = ot.numero
          WHERE COALESCE(bo.valorbase, 0::numeric) > 0::numeric OR COALESCE(bo.total, 0::numeric) > 0::numeric
        ), with_margin AS (
         SELECT pv.id,
            pv.numero,
            pv.numeroloja,
            pv.data,
            pv.totalprodutos,
            pv.total,
            pv.situacao,
            pv.loja,
            pv.store_id,
            pv.itens,
            pv.valorbase,
            pv.custofrete,
            pv.taxacomissao,
            pv.preco_custo,
            pv.item_id,
            pv.itemvalor,
            pv.item_codigo,
            pv.item_produto_id,
            pv.item_descricao,
            pv.item_quantidade,
            pv.item_desconto,
            pv.item_comissao_base,
            pv.item_comissao_valor,
            pv.created_at,
            pv.updated_at,
            pv.bling_id,
            pv.item_index,
            pv.categoria_id,
            pv.categoria_nome,
            pv.verificado,
            pv.status,
            pv.aprovado_por,
            pv.reembolso,
            pv.valorbase_eff,
            pv.total_itemvalor_pedido,
            pv.total_items,
            pv.total_valorbase_pedido,
            pv.item_proportion,
            pv.total_proporcional,
            pv.valorbase_proporcional,
            pv.custofrete_proporcional,
            pv.taxacomissao_proporcional,
                CASE
                    WHEN COALESCE(pv.preco_custo::numeric(10,2), 0::numeric(10,2)) > 0::numeric AND COALESCE(pv.item_quantidade, 0) > 0 AND pv.valorbase_proporcional > 0::numeric THEN (pv.valorbase_proporcional - (pv.custofrete_proporcional + pv.taxacomissao_proporcional) - pv.preco_custo::numeric(10,2) * pv.item_quantidade::numeric + COALESCE(pv.reembolso, 0::double precision)::numeric * pv.item_proportion) / (pv.preco_custo::numeric(10,2) * pv.item_quantidade::numeric)
                    ELSE NULL::numeric
                END AS margem
           FROM proportional_values pv
        )
"""

# SELECT vigente (0193) — usado no downgrade.
_SELECT_OLD = r"""
 SELECT wm.id,
    wm.numero,
    wm.numeroloja,
    wm.data,
    wm.totalprodutos,
    wm.total_proporcional AS total,
    wm.situacao,
    sb.nome AS situacao_nome,
    wm.loja,
    COALESCE(s.apelido_override, si.account_name::text) AS loja_nome,
    COALESCE(i.platform::text, s.marketplace::text, si.platform::text) AS marketplace,
    wm.itens,
    wm.valorbase_proporcional AS valorbase,
    wm.custofrete_proporcional AS custofrete,
    wm.taxacomissao_proporcional AS taxacomissao,
    wm.item_id,
    wm.itemvalor,
    wm.item_codigo,
    wm.item_produto_id,
    wm.item_descricao,
    wm.item_quantidade,
    wm.item_desconto,
    wm.item_comissao_base,
    wm.item_comissao_valor,
    wm.created_at AS bo_created_at,
    wm.updated_at AS bo_updated_at,
    wm.bling_id,
    wm.item_proportion,
    wm.total_itemvalor_pedido,
    wm.valorbase AS original_valorbase,
    wm.custofrete AS original_custofrete,
    wm.taxacomissao AS original_taxacomissao,
    wm.preco_custo::numeric(10,2) AS preco_custo,
    wm.categoria_id,
    wm.categoria_nome,
    wm.verificado,
    wm.status,
    wm.aprovado_por,
    wm.margem,
    pp.segment_id AS subtype_id,
    leaf.name AS subtype,
    root.name AS segmento,
    COALESCE(leaf.min_margin, 0.09)::numeric(6,4) AS min_margin,
        CASE
            WHEN wm.margem IS NULL THEN NULL::numeric
            ELSE wm.margem - COALESCE(leaf.min_margin, 0.09)
        END AS margin_floor_diff,
        CASE
            WHEN COALESCE(wm.preco_custo::numeric(10,2), 0::numeric(10,2)) > 0::numeric AND COALESCE(wm.item_quantidade, 0) > 0 AND wm.valorbase_proporcional > 0::numeric THEN wm.valorbase_proporcional - (wm.custofrete_proporcional + wm.taxacomissao_proporcional) - wm.preco_custo::numeric(10,2) * wm.item_quantidade::numeric + COALESCE(wm.reembolso, 0::double precision)::numeric * wm.item_proportion
            ELSE NULL::numeric
        END AS lucro
   FROM with_margin wm
     LEFT JOIN stores s ON s.bling_store_id =
        CASE
            WHEN wm.loja ~ '^[0-9]+$'::text THEN wm.loja::bigint
            ELSE NULL::bigint
        END
     LEFT JOIN store_info si ON si.bling_store_id = NULLIF(wm.loja, ''::text)"""

# SELECT novo: degrau z% -> minima da Queima de estoque no fallback.
_SELECT_NEW = r"""
 SELECT wm.id,
    wm.numero,
    wm.numeroloja,
    wm.data,
    wm.totalprodutos,
    wm.total_proporcional AS total,
    wm.situacao,
    sb.nome AS situacao_nome,
    wm.loja,
    COALESCE(s.apelido_override, si.account_name::text) AS loja_nome,
    COALESCE(i.platform::text, s.marketplace::text, si.platform::text) AS marketplace,
    wm.itens,
    wm.valorbase_proporcional AS valorbase,
    wm.custofrete_proporcional AS custofrete,
    wm.taxacomissao_proporcional AS taxacomissao,
    wm.item_id,
    wm.itemvalor,
    wm.item_codigo,
    wm.item_produto_id,
    wm.item_descricao,
    wm.item_quantidade,
    wm.item_desconto,
    wm.item_comissao_base,
    wm.item_comissao_valor,
    wm.created_at AS bo_created_at,
    wm.updated_at AS bo_updated_at,
    wm.bling_id,
    wm.item_proportion,
    wm.total_itemvalor_pedido,
    wm.valorbase AS original_valorbase,
    wm.custofrete AS original_custofrete,
    wm.taxacomissao AS original_taxacomissao,
    wm.preco_custo::numeric(10,2) AS preco_custo,
    wm.categoria_id,
    wm.categoria_nome,
    wm.verificado,
    wm.status,
    wm.aprovado_por,
    wm.margem,
    pp.segment_id AS subtype_id,
    leaf.name AS subtype,
    root.name AS segmento,
    COALESCE(leaf.min_margin,
        CASE
            WHEN lower(wm.item_codigo) ~~ 'z%'::text THEN qz.min_margin
            ELSE NULL::numeric
        END, 0.09)::numeric(6,4) AS min_margin,
        CASE
            WHEN wm.margem IS NULL THEN NULL::numeric
            ELSE wm.margem - COALESCE(leaf.min_margin,
        CASE
            WHEN lower(wm.item_codigo) ~~ 'z%'::text THEN qz.min_margin
            ELSE NULL::numeric
        END, 0.09)
        END AS margin_floor_diff,
        CASE
            WHEN COALESCE(wm.preco_custo::numeric(10,2), 0::numeric(10,2)) > 0::numeric AND COALESCE(wm.item_quantidade, 0) > 0 AND wm.valorbase_proporcional > 0::numeric THEN wm.valorbase_proporcional - (wm.custofrete_proporcional + wm.taxacomissao_proporcional) - wm.preco_custo::numeric(10,2) * wm.item_quantidade::numeric + COALESCE(wm.reembolso, 0::double precision)::numeric * wm.item_proportion
            ELSE NULL::numeric
        END AS lucro
   FROM with_margin wm
     LEFT JOIN stores s ON s.bling_store_id =
        CASE
            WHEN wm.loja ~ '^[0-9]+$'::text THEN wm.loja::bigint
            ELSE NULL::bigint
        END
     LEFT JOIN store_info si ON si.bling_store_id = NULLIF(wm.loja, ''::text)"""

# Cauda vigente (0193) — usada no downgrade.
_TAIL_OLD = r"""
     LEFT JOIN integrations i ON i.id = s.integration_id
     LEFT JOIN situacao_bling sb ON sb.id::text = wm.situacao
     LEFT JOIN LATERAL ( SELECT v.segment_id
           FROM pricing_product_variant v
          WHERE lower(wm.item_codigo) = v.variant_norm OR lower(wm.item_codigo) ~~ (v.variant_norm || '.%'::text) OR lower(wm.item_codigo) ~~ (v.variant_norm || '+%'::text)
          ORDER BY (
                CASE
                    WHEN lower(wm.item_codigo) = v.variant_norm THEN 1
                    WHEN lower(wm.item_codigo) ~~ (v.variant_norm || '+%'::text) THEN 2
                    ELSE 3
                END), v.vlen DESC, v.segment_id
         LIMIT 1) pp ON true
     LEFT JOIN segments leaf ON leaf.id = pp.segment_id
     LEFT JOIN segments root ON root.id = leaf.parent_id;
"""

# Cauda nova: + JOIN constante qz (1 linha ou nenhuma; nao multiplica).
_TAIL_NEW = r"""
     LEFT JOIN integrations i ON i.id = s.integration_id
     LEFT JOIN situacao_bling sb ON sb.id::text = wm.situacao
     LEFT JOIN LATERAL ( SELECT v.segment_id
           FROM pricing_product_variant v
          WHERE lower(wm.item_codigo) = v.variant_norm OR lower(wm.item_codigo) ~~ (v.variant_norm || '.%'::text) OR lower(wm.item_codigo) ~~ (v.variant_norm || '+%'::text)
          ORDER BY (
                CASE
                    WHEN lower(wm.item_codigo) = v.variant_norm THEN 1
                    WHEN lower(wm.item_codigo) ~~ (v.variant_norm || '+%'::text) THEN 2
                    ELSE 3
                END), v.vlen DESC, v.segment_id
         LIMIT 1) pp ON true
     LEFT JOIN segments leaf ON leaf.id = pp.segment_id
     LEFT JOIN segments root ON root.id = leaf.parent_id
     LEFT JOIN ( SELECT s2.min_margin
           FROM segments s2
          WHERE lower(s2.name) = 'queima de estoque'::text
          LIMIT 1) qz ON true;
"""


def upgrade() -> None:
    op.execute(
        "CREATE OR REPLACE VIEW davinci.vw_bling_pedidos AS" + _CTES + _SELECT_NEW + _TAIL_NEW
    )


def downgrade() -> None:
    op.execute(
        "CREATE OR REPLACE VIEW davinci.vw_bling_pedidos AS" + _CTES + _SELECT_OLD + _TAIL_OLD
    )
