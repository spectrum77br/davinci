"""add business columns to margens table

Revision ID: 0031_margens_columns
Revises: 0030_vw_bling_pedidos_segments
Create Date: 2026-05-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0031_margens_columns"
down_revision: str | None = "0030_vw_bling_pedidos_segments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "margens",
        sa.Column("data", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "margens",
        sa.Column("pedido_bling", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "margens",
        sa.Column("pedido_plataforma", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column("margens", sa.Column("conta", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column("margens", sa.Column("sku", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column("margens", sa.Column("produtos", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column("margens", sa.Column("custo", sa.Float(), nullable=True), schema=SCHEMA)
    op.add_column("margens", sa.Column("margem", sa.Float(), nullable=True), schema=SCHEMA)
    op.add_column("margens", sa.Column("status", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column(
        "margens",
        sa.Column("observacao", sa.Text(), nullable=True),
        schema=SCHEMA,
    )

    op.create_index("ix_margens_data", "margens", ["data"], schema=SCHEMA)
    op.create_index("ix_margens_pedido_bling", "margens", ["pedido_bling"], schema=SCHEMA)
    op.create_index(
        "ix_margens_pedido_plataforma",
        "margens",
        ["pedido_plataforma"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_margens_pedido_plataforma", table_name="margens", schema=SCHEMA)
    op.drop_index("ix_margens_pedido_bling", table_name="margens", schema=SCHEMA)
    op.drop_index("ix_margens_data", table_name="margens", schema=SCHEMA)
    for column in (
        "observacao",
        "status",
        "margem",
        "custo",
        "produtos",
        "sku",
        "conta",
        "pedido_plataforma",
        "pedido_bling",
        "data",
    ):
        op.drop_column("margens", column, schema=SCHEMA)
