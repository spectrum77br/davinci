"""store_info.integration_id FK

Revision ID: 0018_store_info_integration
Revises: 0017_userdb_tables
Create Date: 2026-05-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0018_store_info_integration"
down_revision: str | None = "0017_userdb_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.add_column(
        "store_info",
        sa.Column("integration_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_store_info_integration_id_integrations",
        "store_info",
        "integrations",
        ["integration_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_store_info_integration_id",
        "store_info",
        ["integration_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.drop_index(
        "ix_store_info_integration_id", table_name="store_info", schema=SCHEMA
    )
    op.drop_constraint(
        "fk_store_info_integration_id_integrations",
        "store_info",
        type_="foreignkey",
        schema=SCHEMA,
    )
    op.drop_column("store_info", "integration_id", schema=SCHEMA)
