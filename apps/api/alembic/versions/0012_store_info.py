"""store_info table (Fase 9d)

Revision ID: 0012_store_info
Revises: 0011_pricing
Create Date: 2026-05-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012_store_info"
down_revision: str | None = "0011_pricing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')

    op.create_table(
        "store_info",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("platform", sa.String(64), nullable=False),
        sa.Column("segment", sa.String(128), nullable=True),
        sa.Column("freight", sa.String(128), nullable=True),
        sa.Column("cpf_name", sa.String(128), nullable=True),
        sa.Column("account_name", sa.String(128), nullable=True),
        sa.Column("server", sa.String(64), nullable=True),
        sa.Column("cnpj", sa.String(32), nullable=True),
        sa.Column("email", sa.String(256), nullable=True),
        sa.Column("observation", sa.Text(), nullable=True),
        sa.Column("shipping_address", sa.Text(), nullable=True),
        sa.Column("return_address", sa.Text(), nullable=True),
        sa.Column("phone", sa.String(64), nullable=True),
        sa.Column("password_enc", sa.Text(), nullable=True, comment="AES-GCM ciphertext"),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_store_info_user", "store_info", ["user_id"], schema=SCHEMA
    )
    op.execute(
        f'CREATE TRIGGER trg_store_info_updated_at '
        f'BEFORE UPDATE ON "{SCHEMA}".store_info '
        f'FOR EACH ROW EXECUTE FUNCTION "{SCHEMA}".set_updated_at()'
    )


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(
        f'DROP TRIGGER IF EXISTS trg_store_info_updated_at '
        f'ON "{SCHEMA}".store_info'
    )
    op.drop_index("ix_store_info_user", table_name="store_info", schema=SCHEMA)
    op.drop_table("store_info", schema=SCHEMA)
