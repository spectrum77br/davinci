"""controle de estoque: stock_movements, stock_checks, users.stock_tag, products.reserved_stock

Revision ID: 0078_controle_estoque
Revises: 0077_alert_type_tarefa_atribuida
Create Date: 2026-05-21

Foundation for the operator-facing /controle-estoque page:

  * `users.stock_tag` — 2-char operator tag (ci / pi / ra / sa / sp).
    When non-null and the user is not admin, the user is an "operador
    de estoque" — gets isolated to /controle-estoque, only sees products
    whose SKU ends with `.{tag}`.

  * `products.reserved_stock` — cached `saldoFisicoTotal - saldoVirtualTotal`
    from the Bling estoque webhook. Default 0; rows untouched by webhooks
    stay at 0 until the first stock event arrives.

  * `stock_movements` — append-only journal of estoque events. One row
    per Bling webhook (operacao + quantidade). Powers the Entrada/Saída
    columns of the Estoque tab.

  * `stock_checks` — per-user "conferido" checkbox + free-text obs for
    each section (estoque / pedido / envio). One row per
    (user, section, reference_id, reference_date).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0078_controle_estoque"
down_revision: str | None = "0077_alert_type_tarefa_atribuida"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("stock_tag", sa.String(16), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "products",
        sa.Column(
            "reserved_stock",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "stock_movements",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("bling_product_id", sa.BigInteger(), nullable=False),
        sa.Column("sku", sa.Text(), nullable=True),
        sa.Column("product_name", sa.Text(), nullable=True),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tipo", sa.Text(), nullable=False),  # 'E' or 'S'
        sa.Column("quantidade", sa.Integer(), nullable=False),
        sa.Column("observacao", sa.Text(), nullable=True),
        sa.Column("origem", sa.Text(), nullable=True),
        sa.Column("saldo_fisico", sa.Numeric(14, 2), nullable=True),
        sa.Column("saldo_virtual", sa.Numeric(14, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_stock_movements_sku_date", "stock_movements",
        ["sku", "date"], schema=SCHEMA,
    )
    op.create_index(
        "ix_stock_movements_bling_product_id", "stock_movements",
        ["bling_product_id"], schema=SCHEMA,
    )
    op.create_index(
        "ix_stock_movements_tipo_date", "stock_movements",
        ["tipo", "date"], schema=SCHEMA,
    )

    op.create_table(
        "stock_checks",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("section", sa.Text(), nullable=False),  # estoque | pedido | envio
        sa.Column("reference_id", sa.Text(), nullable=False),
        sa.Column("reference_date", sa.Date(), nullable=False),
        sa.Column("conferido", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("observacao", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "section", "reference_id", "reference_date",
                            name="uq_stock_checks_user_section_ref_date"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_stock_checks_user_section", "stock_checks",
        ["user_id", "section", "reference_date"], schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_stock_checks_user_section", table_name="stock_checks", schema=SCHEMA)
    op.drop_table("stock_checks", schema=SCHEMA)
    op.drop_index("ix_stock_movements_tipo_date", table_name="stock_movements", schema=SCHEMA)
    op.drop_index("ix_stock_movements_bling_product_id", table_name="stock_movements", schema=SCHEMA)
    op.drop_index("ix_stock_movements_sku_date", table_name="stock_movements", schema=SCHEMA)
    op.drop_table("stock_movements", schema=SCHEMA)
    op.drop_column("products", "reserved_stock", schema=SCHEMA)
    op.drop_column("users", "stock_tag", schema=SCHEMA)
