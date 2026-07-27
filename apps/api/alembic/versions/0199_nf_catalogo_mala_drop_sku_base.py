# ruff: noqa: E501
"""nf_catalogo_mala: remove sku_base (casamento vira automatico por modelo/familia)

O valor cheio da NF de mala passa a casar pelo MODELO/familia derivada do nome do
produto (M1..M6 -> abs, P1..P6 -> pp, ME1 -> me1, ME2 -> me2), entao o vinculo
manual `sku_base` nao e mais necessario.

Revision ID: 0199_nf_catalogo_mala_drop_sku_base
Revises: 0198_nf_catalogo_mala
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0199_nf_catalogo_mala_drop_sku_base"
down_revision: str | None = "0198_nf_catalogo_mala"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.drop_index("ix_nf_catalogo_mala_sku_base", table_name="nf_catalogo_mala", schema=SCHEMA)
    op.drop_column("nf_catalogo_mala", "sku_base", schema=SCHEMA)


def downgrade() -> None:
    op.add_column(
        "nf_catalogo_mala",
        sa.Column("sku_base", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_nf_catalogo_mala_sku_base",
        "nf_catalogo_mala",
        ["sku_base"],
        schema=SCHEMA,
    )
