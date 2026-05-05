"""baseline auth (users + auth_codes + enums)

Revision ID: 0001_baseline_auth
Revises:
Create Date: 2026-05-05

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_baseline_auth"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')
    op.execute(f'SET search_path TO "{SCHEMA}"')

    user_role = postgresql.ENUM("admin", "user", name="user_role", schema=SCHEMA)
    user_role.create(op.get_bind(), checkfirst=True)

    user_status = postgresql.ENUM(
        "pending", "active", "suspended", name="user_status", schema=SCHEMA
    )
    user_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("open_id", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column(
            "role",
            postgresql.ENUM("admin", "user", name="user_role", schema=SCHEMA, create_type=False),
            nullable=False,
            server_default=sa.text("'user'"),
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "pending", "active", "suspended",
                name="user_status", schema=SCHEMA, create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("tuta", sa.String(255), nullable=True),
        sa.Column("upseller", sa.String(255), nullable=True),
        sa.Column("bling_login", sa.String(255), nullable=True),
        sa.Column("adspower", sa.String(255), nullable=True),
        sa.Column(
            "permissions",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("open_id", name="uq_users_open_id"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        schema=SCHEMA,
    )
    op.create_index("ix_users_email", "users", ["email"], schema=SCHEMA)
    op.create_index("ix_users_open_id", "users", ["open_id"], schema=SCHEMA)
    op.create_index(
        "ix_users_permissions_gin",
        "users",
        ["permissions"],
        postgresql_using="gin",
        schema=SCHEMA,
    )

    op.create_table(
        "auth_codes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("code_hash", sa.String(255), nullable=False),
        sa.Column("prefix", sa.String(8), nullable=False),
        sa.Column("session_nonce", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("ip", postgresql.INET, nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_auth_codes_email_created_at",
        "auth_codes",
        ["email", sa.text("created_at DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_auth_codes_expires_at", "auth_codes", ["expires_at"], schema=SCHEMA
    )

    op.execute(f"""
        CREATE OR REPLACE FUNCTION "{SCHEMA}".set_updated_at() RETURNS TRIGGER AS $$
        BEGIN
          NEW.updated_at = now();
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute(f"""
        CREATE TRIGGER trg_users_updated_at
        BEFORE UPDATE ON "{SCHEMA}".users
        FOR EACH ROW EXECUTE FUNCTION "{SCHEMA}".set_updated_at();
    """)


def downgrade() -> None:
    op.execute(f'DROP TRIGGER IF EXISTS trg_users_updated_at ON "{SCHEMA}".users')
    op.execute(f'DROP FUNCTION IF EXISTS "{SCHEMA}".set_updated_at()')
    op.drop_index("ix_auth_codes_expires_at", table_name="auth_codes", schema=SCHEMA)
    op.drop_index("ix_auth_codes_email_created_at", table_name="auth_codes", schema=SCHEMA)
    op.drop_table("auth_codes", schema=SCHEMA)
    op.drop_index("ix_users_permissions_gin", table_name="users", schema=SCHEMA)
    op.drop_index("ix_users_open_id", table_name="users", schema=SCHEMA)
    op.drop_index("ix_users_email", table_name="users", schema=SCHEMA)
    op.drop_table("users", schema=SCHEMA)
    op.execute(f'DROP TYPE IF EXISTS "{SCHEMA}".user_status')
    op.execute(f'DROP TYPE IF EXISTS "{SCHEMA}".user_role')
