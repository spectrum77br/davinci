"""migrate userdb operational tables (bling_orders, situacao_bling, valuation,
amazon_*_lookup, conciliacao_verificados) + extend products with Bling-native
columns + rewired views vw_bling_pedidos, vw_sugestao_compra.

Userdb is being decommissioned; DaVinci becomes single source of truth.

Revision ID: 0017_userdb_tables
Revises: 0016_integration_platform_tiktok_temu
Create Date: 2026-05-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_userdb_tables"
down_revision: Union[str, None] = "0016_integration_platform_tiktok_temu"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')

    # ----------------------------------------- products: add Bling-native cols
    op.add_column(
        "products",
        sa.Column("saldo_fisico", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "products",
        sa.Column("saldo_virtual_total", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "products",
        sa.Column("tipo", sa.CHAR(length=1), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "products",
        sa.Column("situacao", sa.CHAR(length=1), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "products",
        sa.Column("formato", sa.CHAR(length=1), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "products",
        sa.Column("categoria_nome", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "products",
        sa.Column("full_product", postgresql.JSONB, nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "products",
        sa.Column("full_stock", postgresql.JSONB, nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_products_formato", "products", ["formato"], schema=SCHEMA
    )

    # ---------------------------------------------------------- situacao_bling
    op.create_table(
        "situacao_bling",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        schema=SCHEMA,
    )
    op.execute(
        f'CREATE TRIGGER trg_situacao_bling_updated_at '
        f'BEFORE UPDATE ON "{SCHEMA}".situacao_bling '
        f'FOR EACH ROW EXECUTE FUNCTION "{SCHEMA}".set_updated_at()'
    )

    # ----------------------------------------------------- amazon_*_lookup
    op.create_table(
        "amazon_commission_lookup",
        sa.Column("categoria_id", sa.BigInteger(), primary_key=True),
        sa.Column("pct", sa.Numeric(), nullable=False),
        sa.Column("samples", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=True,
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "amazon_shipping_lookup",
        sa.Column("sku", sa.Text(), primary_key=True),
        sa.Column("frete_unit", sa.Numeric(), nullable=False),
        sa.Column("samples", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=True,
        ),
        schema=SCHEMA,
    )

    # ---------------------------------------------------------------- valuation
    op.create_table(
        "valuation",
        sa.Column(
            "id", sa.BigInteger(),
            sa.Identity(always=False, start=1, increment=1),
            primary_key=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("data", sa.Date(), nullable=True),
        sa.Column("caixa", sa.Float(), nullable=True),
        sa.Column("estoque", sa.Float(), nullable=True),
        sa.Column("receber", sa.Float(), nullable=True),
        sa.Column("rentabilidade", sa.Float(), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_valuation_data", "valuation", ["data"], schema=SCHEMA
    )

    # ------------------------------------------------- conciliacao_verificados
    op.create_table(
        "conciliacao_verificados",
        sa.Column("bling_id", sa.BigInteger(), primary_key=True),
        sa.Column("numero", sa.Text(), nullable=True),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column("margem_pct", sa.Numeric(), nullable=True),
        sa.Column("situacao_origem", sa.Integer(), nullable=True),
        sa.Column("situacao_destino", sa.Integer(), nullable=True),
        sa.Column(
            "verificado_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        schema=SCHEMA,
    )

    # ------------------------------------------------------------- bling_orders
    op.create_table(
        "bling_orders",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True, server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("bling_id", sa.BigInteger(), nullable=True),
        sa.Column("numero", sa.Text(), nullable=True),
        sa.Column("numeroloja", sa.Text(), nullable=True),
        sa.Column("numero_documento", sa.Text(), nullable=True),
        sa.Column("data", sa.DateTime(timezone=True), nullable=True),
        sa.Column("totalprodutos", sa.Numeric(), nullable=True),
        sa.Column("total", sa.Numeric(), nullable=True),
        sa.Column("situacao", sa.Text(), nullable=True),
        sa.Column("loja", sa.Text(), nullable=True),
        sa.Column(
            "store_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.stores.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("itens", postgresql.JSONB, nullable=True),
        sa.Column("valorbase", sa.Numeric(), nullable=True),
        sa.Column("custofrete", sa.Numeric(), nullable=True),
        sa.Column("taxacomissao", sa.Numeric(), nullable=True),
        sa.Column("preco_custo", sa.Float(), nullable=True),
        sa.Column("item_id", sa.BigInteger(), nullable=True),
        sa.Column("item_index", sa.Integer(), nullable=True, server_default=sa.text("0")),
        sa.Column("itemvalor", sa.Numeric(), nullable=True),
        sa.Column("item_codigo", sa.Text(), nullable=True),
        sa.Column("item_produto_id", sa.BigInteger(), nullable=True),
        sa.Column("item_descricao", sa.Text(), nullable=True),
        sa.Column("item_quantidade", sa.Integer(), nullable=True),
        sa.Column("item_desconto", sa.Numeric(), nullable=True),
        sa.Column("item_comissao_base", sa.Numeric(), nullable=True),
        sa.Column("item_comissao_valor", sa.Numeric(), nullable=True),
        sa.Column("categoria_id", sa.BigInteger(), nullable=True),
        sa.Column("categoria_nome", sa.Text(), nullable=True),
        sa.Column("em_andamento_data", sa.Date(), nullable=True),
        sa.Column("check", sa.Boolean(), nullable=True, server_default=sa.text("false")),
        sa.Column("devolvido", sa.Boolean(), nullable=True, server_default=sa.text("false")),
        sa.Column("verificado", sa.Boolean(), nullable=True, server_default=sa.text("false")),
        sa.Column("taxas_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("amazon_taxas_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("amazon_lookup_applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_bling_orders_numero", "bling_orders", ["numero"], schema=SCHEMA)
    op.create_index("ix_bling_orders_bling_id", "bling_orders", ["bling_id"], schema=SCHEMA)
    op.create_index("ix_bling_orders_data", "bling_orders", ["data"], schema=SCHEMA)
    op.create_index("ix_bling_orders_store_id", "bling_orders", ["store_id"], schema=SCHEMA)
    op.create_index("ix_bling_orders_situacao", "bling_orders", ["situacao"], schema=SCHEMA)
    op.create_index("ix_bling_orders_item_codigo", "bling_orders", ["item_codigo"], schema=SCHEMA)
    op.create_index(
        "ix_bling_orders_loja_situacao_data",
        "bling_orders", ["loja", "situacao", "data"], schema=SCHEMA,
    )

    op.execute(
        f'CREATE TRIGGER trg_bling_orders_updated_at '
        f'BEFORE UPDATE ON "{SCHEMA}".bling_orders '
        f'FOR EACH ROW EXECUTE FUNCTION "{SCHEMA}".set_updated_at()'
    )

    # ---------------------------------------------------------- vw_bling_pedidos
    # Rewired: lojas → davinci.stores via bling_orders.store_id (FK), with
    # loja_nome from stores.apelido_override and marketplace from stores.marketplace.
    op.execute(f"""
    CREATE VIEW "{SCHEMA}".vw_bling_pedidos AS
    WITH order_totals AS (
        SELECT
            bo.numero,
            count(*) AS total_items,
            sum(COALESCE(bo.itemvalor, 0::numeric)) AS total_itemvalor_pedido,
            max(COALESCE(NULLIF(bo.valorbase, 0::numeric), bo.total, 0::numeric))
                AS total_valorbase_pedido
        FROM "{SCHEMA}".bling_orders bo
        WHERE (bo.item_index > 0 OR (bo.item_index = 0 AND bo.itemvalor IS NOT NULL))
          AND (COALESCE(bo.valorbase, 0::numeric) > 0 OR COALESCE(bo.total, 0::numeric) > 0)
        GROUP BY bo.numero
    ),
    proportional_values AS (
        SELECT
            bo.id, bo.numero, bo.numeroloja, bo.data, bo.totalprodutos, bo.total,
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
                WHEN ot.total_itemvalor_pedido > 0 AND bo.itemvalor IS NOT NULL
                    THEN bo.itemvalor / ot.total_itemvalor_pedido
                ELSE 1.0 / COALESCE(ot.total_items, 1::bigint)::numeric
            END AS item_proportion,
            CASE
                WHEN COALESCE(ot.total_items, 1::bigint) = 1 THEN bo.total
                WHEN ot.total_itemvalor_pedido > 0 AND bo.itemvalor IS NOT NULL
                    THEN bo.total * (bo.itemvalor / ot.total_itemvalor_pedido)
                ELSE bo.total / COALESCE(ot.total_items, 1::bigint)::numeric
            END AS total_proporcional,
            CASE
                WHEN COALESCE(ot.total_items, 1::bigint) = 1
                    THEN COALESCE(NULLIF(bo.valorbase, 0::numeric), bo.total, 0::numeric)
                WHEN ot.total_itemvalor_pedido > 0 AND bo.itemvalor IS NOT NULL
                    THEN COALESCE(NULLIF(bo.valorbase, 0::numeric), bo.total, 0::numeric)
                         * (bo.itemvalor / ot.total_itemvalor_pedido)
                ELSE COALESCE(NULLIF(bo.valorbase, 0::numeric), bo.total, 0::numeric)
                     / COALESCE(ot.total_items, 1::bigint)::numeric
            END AS valorbase_proporcional,
            CASE
                WHEN COALESCE(ot.total_items, 1::bigint) = 1 THEN bo.custofrete
                WHEN ot.total_itemvalor_pedido > 0 AND bo.itemvalor IS NOT NULL
                    THEN bo.custofrete * (bo.itemvalor / ot.total_itemvalor_pedido)
                ELSE bo.custofrete / COALESCE(ot.total_items, 1::bigint)::numeric
            END AS custofrete_proporcional,
            CASE
                WHEN COALESCE(ot.total_items, 1::bigint) = 1 THEN bo.taxacomissao
                WHEN ot.total_itemvalor_pedido > 0 AND bo.itemvalor IS NOT NULL
                    THEN bo.taxacomissao * (bo.itemvalor / ot.total_itemvalor_pedido)
                ELSE bo.taxacomissao / COALESCE(ot.total_items, 1::bigint)::numeric
            END AS taxacomissao_proporcional
        FROM "{SCHEMA}".bling_orders bo
        LEFT JOIN order_totals ot ON bo.numero = ot.numero
        WHERE COALESCE(bo.valorbase, 0::numeric) > 0
           OR COALESCE(bo.total, 0::numeric) > 0
    )
    SELECT
        pv.id, pv.numero, pv.numeroloja, pv.data, pv.totalprodutos,
        pv.total_proporcional AS total,
        pv.situacao,
        sb.nome AS situacao_nome,
        pv.loja,
        s.apelido_override AS loja_nome,
        s.marketplace::text AS marketplace,
        pv.itens,
        pv.valorbase_proporcional AS valorbase,
        pv.custofrete_proporcional AS custofrete,
        pv.taxacomissao_proporcional AS taxacomissao,
        pv.item_id, pv.itemvalor, pv.item_codigo, pv.item_produto_id, pv.item_descricao,
        pv.item_quantidade, pv.item_desconto, pv.item_comissao_base, pv.item_comissao_valor,
        pv.created_at AS bo_created_at,
        pv.updated_at AS bo_updated_at,
        pv.bling_id,
        pv.item_proportion,
        pv.total_itemvalor_pedido,
        pv.valorbase AS original_valorbase,
        pv.custofrete AS original_custofrete,
        pv.taxacomissao AS original_taxacomissao,
        pv.preco_custo::numeric(10,2) AS preco_custo,
        pv.categoria_id,
        pv.categoria_nome,
        pv."check",
        CASE
            WHEN COALESCE(pv.preco_custo::numeric(10,2), 0::numeric(10,2)) > 0
                 AND COALESCE(pv.item_quantidade, 0) > 0
                 AND pv.valorbase_proporcional > 0
            THEN ((pv.valorbase_proporcional - (pv.custofrete_proporcional + pv.taxacomissao_proporcional))
                  - (pv.preco_custo::numeric(10,2) * pv.item_quantidade::numeric))
                 / (pv.preco_custo::numeric(10,2) * pv.item_quantidade::numeric)
            ELSE NULL::numeric
        END AS margem
    FROM proportional_values pv
    LEFT JOIN "{SCHEMA}".stores s ON s.id = pv.store_id
    LEFT JOIN "{SCHEMA}".situacao_bling sb ON sb.id::text = pv.situacao
    """)

    # ----------------------------------------------------- vw_sugestao_compra
    # Rewired: produtos → davinci.products. Filter formato='S' preserved (col
    # added above).  produtos.codigo → products.sku, saldo_fisico kept native.
    op.execute(f"""
    CREATE VIEW "{SCHEMA}".vw_sugestao_compra AS
    WITH vendas_agrupadas AS (
        SELECT
            regexp_replace(bo.item_codigo, '[+.].*$', '') AS codigo_base,
            sum(bo.item_quantidade) AS vendas_ultimos_7_dias
        FROM "{SCHEMA}".bling_orders bo
        WHERE bo.data >= (CURRENT_DATE - interval '7 days')
          AND bo.item_codigo IS NOT NULL
          AND bo.item_quantidade > 0
        GROUP BY regexp_replace(bo.item_codigo, '[+.].*$', '')
    ),
    estoque_agrupado AS (
        SELECT
            regexp_replace(p.sku, '[+.].*$', '') AS codigo_base,
            sum(COALESCE(p.saldo_fisico, 0))::bigint AS estoque_atual
        FROM "{SCHEMA}".products p
        WHERE p.formato = 'S'
        GROUP BY regexp_replace(p.sku, '[+.].*$', '')
    )
    SELECT
        COALESCE(v.codigo_base, e.codigo_base) AS codigo_base,
        COALESCE(v.vendas_ultimos_7_dias, 0::bigint) AS vendas_ultimos_7_dias,
        COALESCE(e.estoque_atual, 0::bigint) AS estoque_atual,
        COALESCE(v.vendas_ultimos_7_dias, 0::bigint)
            - COALESCE(e.estoque_atual, 0::bigint) AS sugestao_compra
    FROM vendas_agrupadas v
    FULL JOIN estoque_agrupado e ON v.codigo_base = e.codigo_base
    WHERE COALESCE(v.codigo_base, e.codigo_base) IS NOT NULL
    """)


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')

    op.execute(f'DROP VIEW IF EXISTS "{SCHEMA}".vw_sugestao_compra')
    op.execute(f'DROP VIEW IF EXISTS "{SCHEMA}".vw_bling_pedidos')

    op.execute(f'DROP TRIGGER IF EXISTS trg_bling_orders_updated_at ON "{SCHEMA}".bling_orders')
    op.drop_index("ix_bling_orders_loja_situacao_data", table_name="bling_orders", schema=SCHEMA)
    op.drop_index("ix_bling_orders_item_codigo", table_name="bling_orders", schema=SCHEMA)
    op.drop_index("ix_bling_orders_situacao", table_name="bling_orders", schema=SCHEMA)
    op.drop_index("ix_bling_orders_store_id", table_name="bling_orders", schema=SCHEMA)
    op.drop_index("ix_bling_orders_data", table_name="bling_orders", schema=SCHEMA)
    op.drop_index("ix_bling_orders_bling_id", table_name="bling_orders", schema=SCHEMA)
    op.drop_index("ix_bling_orders_numero", table_name="bling_orders", schema=SCHEMA)
    op.drop_table("bling_orders", schema=SCHEMA)

    op.drop_table("conciliacao_verificados", schema=SCHEMA)

    op.drop_index("ix_valuation_data", table_name="valuation", schema=SCHEMA)
    op.drop_table("valuation", schema=SCHEMA)

    op.drop_table("amazon_shipping_lookup", schema=SCHEMA)
    op.drop_table("amazon_commission_lookup", schema=SCHEMA)

    op.execute(f'DROP TRIGGER IF EXISTS trg_situacao_bling_updated_at ON "{SCHEMA}".situacao_bling')
    op.drop_table("situacao_bling", schema=SCHEMA)

    op.drop_index("ix_products_formato", table_name="products", schema=SCHEMA)
    for col in (
        "full_stock", "full_product", "categoria_nome",
        "formato", "situacao", "tipo",
        "saldo_virtual_total", "saldo_fisico",
    ):
        op.drop_column("products", col, schema=SCHEMA)
