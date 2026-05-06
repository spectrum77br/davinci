"""integrations + oauth_states + FK stores.integration_id (Fase 2)

Revision ID: 0003_integrations_oauth
Revises: 0002_companies_stores_cadastros
Create Date: 2026-05-05

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_integrations_oauth"
down_revision: Union[str, None] = "0002_companies_stores_cadastros"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "davinci"

PLATFORMS = ("bling", "ml", "shopee", "amazon")


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')

    platform = postgresql.ENUM(*PLATFORMS, name="integration_platform", schema=SCHEMA)
    platform.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "integrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "platform",
            postgresql.ENUM(*PLATFORMS, name="integration_platform",
                            schema=SCHEMA, create_type=False),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("credentials", postgresql.BYTEA(), nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'active'")),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_ok", sa.Boolean(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], [f"{SCHEMA}.users.id"],
            ondelete="CASCADE", name="fk_integrations_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"], [f"{SCHEMA}.stores.id"],
            ondelete="SET NULL", name="fk_integrations_store_id_stores",
        ),
        sa.UniqueConstraint("store_id", name="uq_integrations_store_id"),
        schema=SCHEMA,
    )
    op.create_index("ix_integrations_user_id", "integrations", ["user_id"], schema=SCHEMA)
    op.create_index("ix_integrations_platform", "integrations", ["platform"], schema=SCHEMA)

    # Backfill FK on stores.integration_id (column exists from 0002).
    op.create_foreign_key(
        "fk_stores_integration_id_integrations",
        "stores",
        "integrations",
        ["integration_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )

    op.create_table(
        "oauth_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("state", sa.String(128), nullable=False),
        sa.Column(
            "platform",
            postgresql.ENUM(*PLATFORMS, name="integration_platform",
                            schema=SCHEMA, create_type=False),
            nullable=False,
        ),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code_verifier", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], [f"{SCHEMA}.users.id"],
            ondelete="CASCADE", name="fk_oauth_states_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"], [f"{SCHEMA}.stores.id"],
            ondelete="CASCADE", name="fk_oauth_states_store_id_stores",
        ),
        sa.UniqueConstraint("state", name="uq_oauth_states_state"),
        schema=SCHEMA,
    )
    op.create_index("ix_oauth_states_expires_at", "oauth_states", ["expires_at"], schema=SCHEMA)

    op.execute(f"""
        CREATE TRIGGER trg_integrations_updated_at
        BEFORE UPDATE ON "{SCHEMA}".integrations
        FOR EACH ROW EXECUTE FUNCTION "{SCHEMA}".set_updated_at();
    """)


def downgrade() -> None:
    op.execute(f'DROP TRIGGER IF EXISTS trg_integrations_updated_at ON "{SCHEMA}".integrations')
    op.drop_index("ix_oauth_states_expires_at", table_name="oauth_states", schema=SCHEMA)
    op.drop_table("oauth_states", schema=SCHEMA)
    op.drop_constraint(
        "fk_stores_integration_id_integrations",
        "stores",
        schema=SCHEMA,
        type_="foreignkey",
    )
    op.drop_index("ix_integrations_platform", table_name="integrations", schema=SCHEMA)
    op.drop_index("ix_integrations_user_id", table_name="integrations", schema=SCHEMA)
    op.drop_table("integrations", schema=SCHEMA)
    op.execute(f'DROP TYPE IF EXISTS "{SCHEMA}".integration_platform')
