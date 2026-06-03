# ruff: noqa: E501
"""bling_kit_components: cache local da composição (estrutura) dos kits Bling

Espelha `estrutura.componentes` dos produtos compostos (formato='E') do Bling,
populado semanalmente. O order-lookup de devoluções usa essa tabela pra explodir
um SKU de kit (ex.: `b011`) nos componentes individuais — a composição não está
na string do SKU, então sem esse cache o estoque não volta pros produtos certos.

Revision ID: 0127_bling_kit_components
Revises: 0126_vw_devolucoes_resolvido
Create Date: 2026-06-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0127_bling_kit_components"
down_revision: str | None = "0126_vw_devolucoes_resolvido"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
TABLE = "bling_kit_components"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("kit_bling_product_id", sa.BigInteger(), nullable=False),
        sa.Column("component_bling_product_id", sa.BigInteger(), nullable=False),
        sa.Column("quantidade", sa.Numeric(12, 4), nullable=False, server_default=sa.text("1")),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint(
            "kit_bling_product_id", "component_bling_product_id",
            name="uq_bling_kit_components_kit_comp",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_bling_kit_components_kit_bling_product_id",
        TABLE, ["kit_bling_product_id"], schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_bling_kit_components_kit_bling_product_id", table_name=TABLE, schema=SCHEMA)
    op.drop_table(TABLE, schema=SCHEMA)
