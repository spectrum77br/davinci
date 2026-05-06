"""pricing accounts/products/overrides + audit dismissed + idempotency (Fase 9a)

Revision ID: 0011_pricing
Revises: 0010_listings
Create Date: 2026-05-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_pricing"
down_revision: Union[str, None] = "0010_listings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "davinci"

DEPARTMENTS = ("celular", "mala", "eletro", "catalogo")
PRICING_PLATFORMS = (
    "mercadolivre",
    "shopee",
    "temu",
    "amazon",
    "aliexpress",
    "tiktok",
    "magalu",
)
CELL_STATUSES = ("auto", "manual", "locked", "disabled")


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')

    bind = op.get_bind()
    sa.Enum(*DEPARTMENTS, name="department", schema=SCHEMA).create(bind, checkfirst=True)
    sa.Enum(
        *PRICING_PLATFORMS, name="pricing_platform", schema=SCHEMA
    ).create(bind, checkfirst=True)
    sa.Enum(*CELL_STATUSES, name="cell_status", schema=SCHEMA).create(bind, checkfirst=True)

    # ------------------------------------------------------------- pricing_accounts
    op.create_table(
        "pricing_accounts",
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
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column(
            "platform",
            postgresql.ENUM(
                *PRICING_PLATFORMS,
                name="pricing_platform",
                schema=SCHEMA,
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("listing_type", sa.String(64), nullable=True),
        sa.Column(
            "department",
            postgresql.ENUM(
                *DEPARTMENTS,
                name="department",
                schema=SCHEMA,
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'celular'"),
        ),
        sa.Column("kit_number", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("commission", sa.Numeric(6, 4), nullable=True),
        sa.Column("margin1", sa.Numeric(6, 4), nullable=True),
        sa.Column("shipping1", sa.Numeric(8, 2), nullable=True),
        sa.Column("margin2", sa.Numeric(6, 4), nullable=True),
        sa.Column("shipping2", sa.Numeric(8, 2), nullable=True),
        sa.Column("margin3", sa.Numeric(6, 4), nullable=True),
        sa.Column("shipping3", sa.Numeric(8, 2), nullable=True),
        sa.Column("margin4", sa.Numeric(6, 4), nullable=True),
        sa.Column("shipping4", sa.Numeric(8, 2), nullable=True),
        sa.Column("margin5", sa.Numeric(6, 4), nullable=True),
        sa.Column("shipping5", sa.Numeric(8, 2), nullable=True),
        sa.Column("server", sa.String(64), nullable=True),
        sa.Column("email", sa.String(256), nullable=True),
        sa.Column("password_enc", sa.Text(), nullable=True, comment="AES-GCM ciphertext"),
        sa.Column("phone", sa.String(64), nullable=True),
        sa.Column("shipping_address", sa.Text(), nullable=True),
        sa.Column("return_address", sa.Text(), nullable=True),
        sa.Column("observation", sa.Text(), nullable=True),
        sa.Column("observation2", sa.Text(), nullable=True),
        sa.Column("observation3", sa.Text(), nullable=True),
        sa.Column(
            "store_info_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "integration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.integrations.id", ondelete="SET NULL"),
            nullable=True,
        ),
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
        "ix_pricing_accounts_user_dept",
        "pricing_accounts",
        ["user_id", "department"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_pricing_accounts_integration",
        "pricing_accounts",
        ["integration_id"],
        schema=SCHEMA,
    )
    op.execute(
        f'CREATE TRIGGER trg_pricing_accounts_updated_at '
        f'BEFORE UPDATE ON "{SCHEMA}".pricing_accounts '
        f'FOR EACH ROW EXECUTE FUNCTION "{SCHEMA}".set_updated_at()'
    )

    # ------------------------------------------------------------- pricing_products
    op.create_table(
        "pricing_products",
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
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.products.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("sku", sa.String(2048), nullable=False),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column(
            "department",
            postgresql.ENUM(
                *DEPARTMENTS,
                name="department",
                schema=SCHEMA,
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'celular'"),
        ),
        sa.Column("product_type", sa.Integer(), nullable=False, server_default=sa.text("2")),
        sa.Column("bling_cost_price", sa.Numeric(10, 2), nullable=True),
        sa.Column("cost_kit1", sa.Numeric(10, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("cost_kit2", sa.Numeric(10, 2), nullable=True),
        sa.Column("cost_kit3", sa.Numeric(10, 2), nullable=True),
        sa.Column("cost_kit4", sa.Numeric(10, 2), nullable=True),
        sa.Column("description", sa.String(256), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("ean", sa.String(64), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "in_catalog",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="Marca produto como elegivel ao catalogo ML",
        ),
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
        sa.UniqueConstraint("user_id", "sku", name="uq_pricing_products_user_sku"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_pricing_products_user_dept",
        "pricing_products",
        ["user_id", "department"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_pricing_products_product",
        "pricing_products",
        ["product_id"],
        schema=SCHEMA,
    )
    op.execute(
        f'CREATE TRIGGER trg_pricing_products_updated_at '
        f'BEFORE UPDATE ON "{SCHEMA}".pricing_products '
        f'FOR EACH ROW EXECUTE FUNCTION "{SCHEMA}".set_updated_at()'
    )

    # ------------------------------------------------------------ pricing_overrides
    op.create_table(
        "pricing_overrides",
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
        sa.Column(
            "pricing_product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.pricing_products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pricing_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.pricing_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("price_override", sa.Numeric(10, 2), nullable=True),
        sa.Column(
            "cell_status",
            postgresql.ENUM(
                *CELL_STATUSES,
                name="cell_status",
                schema=SCHEMA,
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'auto'"),
        ),
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
        sa.UniqueConstraint(
            "pricing_product_id",
            "pricing_account_id",
            name="uq_pricing_overrides_product_account",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_pricing_overrides_user",
        "pricing_overrides",
        ["user_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_pricing_overrides_account",
        "pricing_overrides",
        ["pricing_account_id"],
        schema=SCHEMA,
    )
    op.execute(
        f'CREATE TRIGGER trg_pricing_overrides_updated_at '
        f'BEFORE UPDATE ON "{SCHEMA}".pricing_overrides '
        f'FOR EACH ROW EXECUTE FUNCTION "{SCHEMA}".set_updated_at()'
    )

    # --------------------------------------------------------- audit_dismissed_skus
    op.create_table(
        "audit_dismissed_skus",
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
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column(
            "dismissed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "sku", name="uq_audit_dismissed_skus_user_sku"),
        schema=SCHEMA,
    )

    # ----------------------------------------------------- pricing_push_idempotency
    # Cache request->response per Idempotency-Key for 24h (resolves B13).
    op.create_table(
        "pricing_push_idempotency",
        sa.Column(
            "key",
            sa.String(128),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column(
            "response",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_pricing_push_idempotency_expires",
        "pricing_push_idempotency",
        ["expires_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')

    op.drop_index(
        "ix_pricing_push_idempotency_expires",
        table_name="pricing_push_idempotency",
        schema=SCHEMA,
    )
    op.drop_table("pricing_push_idempotency", schema=SCHEMA)

    op.drop_table("audit_dismissed_skus", schema=SCHEMA)

    op.execute(
        f'DROP TRIGGER IF EXISTS trg_pricing_overrides_updated_at '
        f'ON "{SCHEMA}".pricing_overrides'
    )
    op.drop_index(
        "ix_pricing_overrides_account", table_name="pricing_overrides", schema=SCHEMA
    )
    op.drop_index(
        "ix_pricing_overrides_user", table_name="pricing_overrides", schema=SCHEMA
    )
    op.drop_table("pricing_overrides", schema=SCHEMA)

    op.execute(
        f'DROP TRIGGER IF EXISTS trg_pricing_products_updated_at '
        f'ON "{SCHEMA}".pricing_products'
    )
    op.drop_index(
        "ix_pricing_products_product", table_name="pricing_products", schema=SCHEMA
    )
    op.drop_index(
        "ix_pricing_products_user_dept", table_name="pricing_products", schema=SCHEMA
    )
    op.drop_table("pricing_products", schema=SCHEMA)

    op.execute(
        f'DROP TRIGGER IF EXISTS trg_pricing_accounts_updated_at '
        f'ON "{SCHEMA}".pricing_accounts'
    )
    op.drop_index(
        "ix_pricing_accounts_integration",
        table_name="pricing_accounts",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_pricing_accounts_user_dept",
        table_name="pricing_accounts",
        schema=SCHEMA,
    )
    op.drop_table("pricing_accounts", schema=SCHEMA)

    bind = op.get_bind()
    sa.Enum(name="cell_status", schema=SCHEMA).drop(bind, checkfirst=True)
    sa.Enum(name="pricing_platform", schema=SCHEMA).drop(bind, checkfirst=True)
    sa.Enum(name="department", schema=SCHEMA).drop(bind, checkfirst=True)
