# ruff: noqa: E501, S608
"""add bling_orders approval fields and remove legacy flags

Revision ID: 0033_bling_orders_approval_status
Revises: 0032_vw_bling_pedidos_verificado
Create Date: 2026-05-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0033_bling_orders_approval_status"
down_revision: str | None = "0032_vw_bling_pedidos_verificado"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def _view_sql(*, include_legacy_check: bool, include_approval_fields: bool) -> str:
    check_select = ', bo."check"' if include_legacy_check else ""
    check_projection = ', wm."check"' if include_legacy_check else ""
    approval_select = ", bo.status, bo.aprovado_por" if include_approval_fields else ""
    approval_projection = ", wm.status, wm.aprovado_por" if include_approval_fields else ""

    return f"""
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
                bo.bling_id, bo.item_index, bo.categoria_id, bo.categoria_nome{check_select},
                bo.verificado{approval_select},
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
        wm.categoria_id, wm.categoria_nome{check_projection},
        wm.verificado{approval_projection},
        wm.margem,
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
    op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.vw_bling_pedidos CASCADE")

    op.add_column("bling_orders", sa.Column("status", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column(
        "bling_orders",
        sa.Column("aprovado_por", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_bling_orders_aprovado_por_users",
        "bling_orders",
        "users",
        ["aprovado_por"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )
    op.create_index("ix_bling_orders_status", "bling_orders", ["status"], schema=SCHEMA)
    op.create_index(
        "ix_bling_orders_aprovado_por",
        "bling_orders",
        ["aprovado_por"],
        schema=SCHEMA,
    )

    op.drop_column("bling_orders", "check", schema=SCHEMA)
    op.drop_column("bling_orders", "devolvido", schema=SCHEMA)

    op.execute(_view_sql(include_legacy_check=False, include_approval_fields=True))


def downgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.vw_bling_pedidos CASCADE")

    op.add_column(
        "bling_orders",
        sa.Column("check", sa.Boolean(), nullable=True, server_default=sa.text("false")),
        schema=SCHEMA,
    )
    op.add_column(
        "bling_orders",
        sa.Column("devolvido", sa.Boolean(), nullable=True, server_default=sa.text("false")),
        schema=SCHEMA,
    )

    op.drop_index("ix_bling_orders_aprovado_por", table_name="bling_orders", schema=SCHEMA)
    op.drop_index("ix_bling_orders_status", table_name="bling_orders", schema=SCHEMA)
    op.drop_constraint(
        "fk_bling_orders_aprovado_por_users",
        "bling_orders",
        schema=SCHEMA,
        type_="foreignkey",
    )
    op.drop_column("bling_orders", "aprovado_por", schema=SCHEMA)
    op.drop_column("bling_orders", "status", schema=SCHEMA)

    op.execute(_view_sql(include_legacy_check=True, include_approval_fields=False))
