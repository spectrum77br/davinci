# ruff: noqa: E501, S608
"""create marketplace margin reconciliation view

Revision ID: 0045_vw_conciliacao_margens
Revises: 0044_marketplace_financials
Create Date: 2026-05-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0045_vw_conciliacao_margens"
down_revision: str | None = "0044_marketplace_financials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
VIEW_NAME = "vw_conciliacao_margens_marketplace"


VIEW_SQL = f"""
CREATE OR REPLACE VIEW {SCHEMA}.{VIEW_NAME} AS
WITH event_amounts AS (
    SELECT
        e.order_financial_id,
        e.event_type,
        SUM(e.amount) AS amount
    FROM {SCHEMA}.marketplace_financial_events e
    GROUP BY e.order_financial_id, e.event_type
),
event_totals AS (
    SELECT
        ea.order_financial_id,
        COUNT(*) AS marketplace_event_type_count,
        jsonb_object_agg(ea.event_type, ea.amount ORDER BY ea.event_type) AS marketplace_eventos,
        SUM(ea.amount) FILTER (WHERE ea.event_type = 'sale') AS evento_sale,
        SUM(ea.amount) FILTER (WHERE ea.event_type = 'commission_fee') AS evento_commission_fee,
        SUM(ea.amount) FILTER (WHERE ea.event_type = 'service_fee') AS evento_service_fee,
        SUM(ea.amount) FILTER (WHERE ea.event_type = 'freight') AS evento_freight,
        SUM(ea.amount) FILTER (WHERE ea.event_type = 'shipping_rebate') AS evento_shipping_rebate,
        SUM(ea.amount) FILTER (WHERE ea.event_type = 'discount') AS evento_discount,
        SUM(ea.amount) FILTER (WHERE ea.event_type = 'refund') AS evento_refund,
        SUM(ea.amount) FILTER (WHERE ea.event_type = 'tax') AS evento_tax,
        SUM(ea.amount) FILTER (WHERE ea.event_type = 'adjustment') AS evento_adjustment,
        SUM(ea.amount) FILTER (WHERE ea.event_type = 'net_payout') AS evento_net_payout,
        SUM(ea.amount) FILTER (WHERE ea.event_type = 'net_estimated') AS evento_net_estimated
    FROM event_amounts ea
    GROUP BY ea.order_financial_id
),
joined AS (
    SELECT
        bp.id AS bling_order_item_id,
        bp.bling_id,
        bp.numero AS pedido_bling,
        bp.numeroloja AS pedido_marketplace,
        bp.data,
        bp.situacao,
        bp.situacao_nome,
        bp.loja AS bling_loja_id,
        bp.loja_nome,
        bp.marketplace AS plataforma_bling,
        bp.item_codigo AS sku,
        bp.item_produto_id,
        bp.item_descricao AS produto,
        bp.item_quantidade AS quantidade,
        bp.itemvalor AS item_valor_original,
        bp.item_desconto,
        bp.item_proportion,
        bp.total_itemvalor_pedido AS bling_total_itemvalor_pedido,
        bp.original_valorbase AS bling_valorbase_pedido,
        bp.original_custofrete AS bling_custofrete_pedido,
        bp.original_taxacomissao AS bling_taxacomissao_pedido,
        bp.valorbase AS bling_valorbase_item,
        bp.custofrete AS bling_custofrete_item,
        bp.taxacomissao AS bling_taxacomissao_item,
        bp.preco_custo AS bling_preco_custo_unitario,
        (bp.preco_custo * bp.item_quantidade::numeric) AS bling_custo_produtos,
        bp.lucro AS bling_lucro,
        bp.margem AS bling_margem,
        bp.min_margin AS margem_minima,
        bp.margin_floor_diff AS bling_margem_vs_minima,
        bp.segmento,
        bp.subtype,
        bp.categoria_id,
        bp.categoria_nome,
        bp.verificado,
        bp.status AS bling_status_margem,
        bp.aprovado_por,
        f.id AS financeiro_id,
        f.platform::text AS plataforma_financeiro,
        f.integration_id AS financeiro_integration_id,
        f.store_id AS financeiro_store_id,
        f.external_order_id AS financeiro_pedido_marketplace,
        f.status AS financeiro_status,
        f.currency AS financeiro_moeda,
        f.fetched_at AS financeiro_atualizado_em,
        f.next_retry_at AS financeiro_proxima_tentativa_em,
        f.attempts AS financeiro_tentativas,
        f.last_error AS financeiro_ultimo_erro,
        f.gross_amount AS marketplace_valor_bruto_pedido,
        f.fee_amount AS marketplace_taxas_pedido,
        f.freight_amount AS marketplace_frete_pedido,
        f.rebate_amount AS marketplace_rebate_pedido,
        f.discount_amount AS marketplace_desconto_pedido,
        f.refund_amount AS marketplace_reembolso_pedido,
        f.tax_amount AS marketplace_imposto_pedido,
        f.adjustment_amount AS marketplace_ajuste_pedido,
        f.net_amount AS marketplace_liquido_pedido,
        COALESCE(
            f.net_amount,
            f.gross_amount
                - COALESCE(f.fee_amount, 0)
                - COALESCE(f.freight_amount, 0)
                + COALESCE(f.rebate_amount, 0)
                - COALESCE(f.discount_amount, 0)
                - COALESCE(f.refund_amount, 0)
                - COALESCE(f.tax_amount, 0)
                + COALESCE(f.adjustment_amount, 0)
        ) AS marketplace_liquido_base_margem_pedido,
        et.marketplace_event_type_count,
        et.marketplace_eventos,
        et.evento_sale,
        et.evento_commission_fee,
        et.evento_service_fee,
        et.evento_freight,
        et.evento_shipping_rebate,
        et.evento_discount,
        et.evento_refund,
        et.evento_tax,
        et.evento_adjustment,
        et.evento_net_payout,
        et.evento_net_estimated
    FROM (
        SELECT *
        FROM {SCHEMA}.vw_bling_pedidos
        WHERE data >= now() - interval '30 days'
    ) bp
    LEFT JOIN LATERAL (
        SELECT mf.*
        FROM {SCHEMA}.marketplace_order_financials mf
        WHERE mf.bling_id = bp.bling_id
          AND (
              bp.numeroloja IS NULL
              OR mf.external_order_id = bp.numeroloja
          )
        ORDER BY
            CASE WHEN mf.external_order_id = bp.numeroloja THEN 0 ELSE 1 END,
            mf.fetched_at DESC NULLS LAST,
            mf.created_at DESC
        LIMIT 1
    ) f ON TRUE
    LEFT JOIN event_totals et ON et.order_financial_id = f.id
)
SELECT
    j.bling_order_item_id,
    j.bling_id,
    j.pedido_bling,
    j.pedido_marketplace,
    j.data,
    j.situacao,
    j.situacao_nome,
    j.bling_loja_id,
    j.loja_nome,
    j.plataforma_bling,
    j.plataforma_financeiro,
    j.sku,
    j.item_produto_id,
    j.produto,
    j.quantidade,
    j.item_valor_original,
    j.item_desconto,
    j.item_proportion,
    j.bling_total_itemvalor_pedido,
    j.bling_valorbase_pedido,
    j.bling_custofrete_pedido,
    j.bling_taxacomissao_pedido,
    j.bling_valorbase_item,
    j.bling_custofrete_item,
    j.bling_taxacomissao_item,
    j.bling_preco_custo_unitario,
    j.bling_custo_produtos,
    j.bling_lucro,
    j.bling_margem,
    j.margem_minima,
    j.bling_margem_vs_minima,
    j.segmento,
    j.subtype,
    j.categoria_id,
    j.categoria_nome,
    j.verificado,
    j.bling_status_margem,
    j.aprovado_por,
    j.financeiro_id,
    j.financeiro_integration_id,
    j.financeiro_store_id,
    j.financeiro_pedido_marketplace,
    j.financeiro_status,
    j.financeiro_moeda,
    j.financeiro_atualizado_em,
    j.financeiro_proxima_tentativa_em,
    j.financeiro_tentativas,
    j.financeiro_ultimo_erro,
    j.marketplace_valor_bruto_pedido,
    j.marketplace_taxas_pedido,
    j.marketplace_frete_pedido,
    j.marketplace_rebate_pedido,
    j.marketplace_desconto_pedido,
    j.marketplace_reembolso_pedido,
    j.marketplace_imposto_pedido,
    j.marketplace_ajuste_pedido,
    j.marketplace_liquido_pedido,
    j.marketplace_liquido_base_margem_pedido,
    (j.marketplace_valor_bruto_pedido * j.item_proportion) AS marketplace_valor_bruto_item,
    (j.marketplace_taxas_pedido * j.item_proportion) AS marketplace_taxas_item,
    (j.marketplace_frete_pedido * j.item_proportion) AS marketplace_frete_item,
    (j.marketplace_rebate_pedido * j.item_proportion) AS marketplace_rebate_item,
    (j.marketplace_desconto_pedido * j.item_proportion) AS marketplace_desconto_item,
    (j.marketplace_reembolso_pedido * j.item_proportion) AS marketplace_reembolso_item,
    (j.marketplace_imposto_pedido * j.item_proportion) AS marketplace_imposto_item,
    (j.marketplace_ajuste_pedido * j.item_proportion) AS marketplace_ajuste_item,
    (j.marketplace_liquido_pedido * j.item_proportion) AS marketplace_liquido_item,
    (j.marketplace_liquido_base_margem_pedido * j.item_proportion) AS marketplace_liquido_base_margem_item,
    CASE
        WHEN j.marketplace_liquido_base_margem_pedido IS NOT NULL
             AND COALESCE(j.bling_custo_produtos, 0) > 0
        THEN (j.marketplace_liquido_base_margem_pedido * j.item_proportion) - j.bling_custo_produtos
        ELSE NULL::numeric
    END AS marketplace_lucro,
    CASE
        WHEN j.marketplace_liquido_base_margem_pedido IS NOT NULL
             AND COALESCE(j.bling_custo_produtos, 0) > 0
        THEN ((j.marketplace_liquido_base_margem_pedido * j.item_proportion) - j.bling_custo_produtos)
             / j.bling_custo_produtos
        ELSE NULL::numeric
    END AS marketplace_margem,
    CASE
        WHEN j.marketplace_liquido_base_margem_pedido IS NOT NULL
             AND COALESCE(j.bling_custo_produtos, 0) > 0
             AND j.margem_minima IS NOT NULL
        THEN (((j.marketplace_liquido_base_margem_pedido * j.item_proportion) - j.bling_custo_produtos)
             / j.bling_custo_produtos) - j.margem_minima
        ELSE NULL::numeric
    END AS marketplace_margem_vs_minima,
    CASE
        WHEN j.marketplace_liquido_base_margem_pedido IS NOT NULL
             AND COALESCE(j.bling_custo_produtos, 0) > 0
             AND j.bling_lucro IS NOT NULL
        THEN ((j.marketplace_liquido_base_margem_pedido * j.item_proportion) - j.bling_custo_produtos) - j.bling_lucro
        ELSE NULL::numeric
    END AS diferenca_lucro_marketplace_bling,
    CASE
        WHEN j.marketplace_liquido_base_margem_pedido IS NOT NULL
             AND COALESCE(j.bling_custo_produtos, 0) > 0
             AND j.bling_margem IS NOT NULL
        THEN (((j.marketplace_liquido_base_margem_pedido * j.item_proportion) - j.bling_custo_produtos)
             / j.bling_custo_produtos) - j.bling_margem
        ELSE NULL::numeric
    END AS diferenca_margem_marketplace_bling,
    j.marketplace_event_type_count,
    j.marketplace_eventos,
    j.evento_sale,
    j.evento_commission_fee,
    j.evento_service_fee,
    j.evento_freight,
    j.evento_shipping_rebate,
    j.evento_discount,
    j.evento_refund,
    j.evento_tax,
    j.evento_adjustment,
    j.evento_net_payout,
    j.evento_net_estimated
FROM joined j
"""


def upgrade() -> None:
    op.execute(f"SET LOCAL search_path TO {SCHEMA}, public")
    op.execute(VIEW_SQL)
    op.execute(
        f"COMMENT ON VIEW {SCHEMA}.{VIEW_NAME} IS "
        "'Compara dados de margem dos ultimos 30 dias calculados pelo Bling com os financeiros capturados do marketplace, sem classificar divergencias.'"
    )


def downgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.{VIEW_NAME}")
