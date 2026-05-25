# ruff: noqa: E501
"""bling_orders — add nome_destinatario and cep_destino from transporte.

These columns store recipient name and delivery CEP extracted from Bling API
transporte.contato.nome and transporte.enderecoEntrega.cep, enabling devolution
lookup by customer name or postal code when the order number is missing from the
return shipping label.

Revision ID: 0088_bling_orders_transporte
Revises: 0087_pricing_overrides_cell_color
Create Date: 2026-05-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0088_bling_orders_transporte"
down_revision: str | None = "0087_pricing_overrides_cell_color"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "bling_orders",
        sa.Column("nome_destinatario", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "bling_orders",
        sa.Column("cep_destino", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_bling_orders_cep_destino",
        "bling_orders",
        ["cep_destino"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_bling_orders_cep_destino", table_name="bling_orders", schema=SCHEMA)
    op.drop_column("bling_orders", "cep_destino", schema=SCHEMA)
    op.drop_column("bling_orders", "nome_destinatario", schema=SCHEMA)
