"""products + product_links + background_jobs (Fase 3)

Revision ID: 0004_products_links_jobs
Revises: 0003_integrations_oauth
Create Date: 2026-05-05

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_products_links_jobs"
down_revision: Union[str, None] = "0003_integrations_oauth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "davinci"

LINK_SYNC_STATUSES = ("ok", "skipped", "retryable", "fatal", "pending", "requires_review")
JOB_TYPES = (
    "sync_all",
    "sync_product",
    "auto_link",
    "audit",
    "sync_bling_costs",
    "import_listings",
    "import_bling_products",
    "push_prices_batch",
)
JOB_STATUSES = ("pending", "running", "succeeded", "failed", "cancelled")


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')

    link_sync_status = postgresql.ENUM(
        *LINK_SYNC_STATUSES, name="link_sync_status", schema=SCHEMA
    )
    link_sync_status.create(op.get_bind(), checkfirst=True)

    job_type = postgresql.ENUM(*JOB_TYPES, name="background_job_type", schema=SCHEMA)
    job_type.create(op.get_bind(), checkfirst=True)

    job_status = postgresql.ENUM(*JOB_STATUSES, name="background_job_status", schema=SCHEMA)
    job_status.create(op.get_bind(), checkfirst=True)

    # ------------------------------------------------------------------ products
    op.create_table(
        "products",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("cost_price", sa.Numeric(14, 4), nullable=True),
        sa.Column("bling_cost_price", sa.Numeric(14, 4), nullable=True),
        sa.Column("price", sa.Numeric(14, 4), nullable=True),
        sa.Column("stock", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("min_stock", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "bling_product_id",
            sa.BigInteger(),
            nullable=True,
            comment="Bling Api/v3 produto.id (origin master)",
        ),
        sa.Column("integration_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("observation", sa.Text(), nullable=True),
        sa.Column("observation2", sa.Text(), nullable=True),
        sa.Column("observation3", sa.Text(), nullable=True),
        sa.Column("last_imported_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{SCHEMA}.users.id"],
            ondelete="CASCADE",
            name="fk_products_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            [f"{SCHEMA}.integrations.id"],
            ondelete="SET NULL",
            name="fk_products_integration_id_integrations",
        ),
        sa.UniqueConstraint("user_id", "sku", name="uq_products_user_id_sku"),
        sa.CheckConstraint("length(trim(sku)) > 0", name="ck_products_sku_not_blank"),
        schema=SCHEMA,
    )
    op.create_index("ix_products_user_id", "products", ["user_id"], schema=SCHEMA)
    op.create_index("ix_products_integration_id", "products", ["integration_id"], schema=SCHEMA)
    op.create_index(
        "ix_products_bling_product_id",
        "products",
        ["bling_product_id"],
        schema=SCHEMA,
    )

    op.execute(
        f'CREATE TRIGGER trg_products_updated_at '
        f'BEFORE UPDATE ON "{SCHEMA}".products '
        f'FOR EACH ROW EXECUTE FUNCTION "{SCHEMA}".set_updated_at()'
    )

    # ------------------------------------------------------------- product_links
    op.create_table(
        "product_links",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("integration_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "platform",
            postgresql.ENUM(
                "bling", "ml", "shopee", "amazon",
                name="integration_platform",
                schema=SCHEMA,
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("variation_id", sa.Text(), nullable=True),
        sa.Column("external_sku", sa.Text(), nullable=True),
        sa.Column("listing_title", sa.Text(), nullable=True),
        sa.Column("stock", sa.Integer(), nullable=True),
        sa.Column("price", sa.Numeric(14, 4), nullable=True),
        sa.Column(
            "last_sync_status",
            postgresql.ENUM(
                *LINK_SYNC_STATUSES,
                name="link_sync_status",
                schema=SCHEMA,
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{SCHEMA}.users.id"],
            ondelete="CASCADE",
            name="fk_product_links_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            [f"{SCHEMA}.products.id"],
            ondelete="CASCADE",
            name="fk_product_links_product_id_products",
        ),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            [f"{SCHEMA}.integrations.id"],
            ondelete="CASCADE",
            name="fk_product_links_integration_id_integrations",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            [f"{SCHEMA}.stores.id"],
            ondelete="SET NULL",
            name="fk_product_links_store_id_stores",
        ),
        schema=SCHEMA,
    )
    # Composite UNIQUE per PRD §4.1 #4: (user_id, platform, integration_id, external_id, variation_id).
    # Postgres treats NULL as distinct so use COALESCE-based functional unique to dedup variation_id NULL.
    op.create_index(
        "uq_product_links_identity",
        "product_links",
        [
            "user_id",
            "platform",
            "integration_id",
            "external_id",
            sa.text("COALESCE(variation_id, '')"),
        ],
        unique=True,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_product_links_product_id",
        "product_links",
        ["product_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_product_links_integration_id",
        "product_links",
        ["integration_id"],
        schema=SCHEMA,
    )
    op.create_index("ix_product_links_store_id", "product_links", ["store_id"], schema=SCHEMA)

    op.execute(
        f'CREATE TRIGGER trg_product_links_updated_at '
        f'BEFORE UPDATE ON "{SCHEMA}".product_links '
        f'FOR EACH ROW EXECUTE FUNCTION "{SCHEMA}".set_updated_at()'
    )

    # ----------------------------------------------------------- background_jobs
    op.create_table(
        "background_jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "type",
            postgresql.ENUM(
                *JOB_TYPES,
                name="background_job_type",
                schema=SCHEMA,
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                *JOB_STATUSES,
                name="background_job_status",
                schema=SCHEMA,
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("arq_job_id", sa.String(64), nullable=True),
        sa.Column("total", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("processed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "payload",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "result",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "details",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["created_by"],
            [f"{SCHEMA}.users.id"],
            ondelete="CASCADE",
            name="fk_background_jobs_created_by_users",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_background_jobs_created_by",
        "background_jobs",
        ["created_by"],
        schema=SCHEMA,
    )
    op.create_index("ix_background_jobs_status", "background_jobs", ["status"], schema=SCHEMA)
    op.create_index(
        "ix_background_jobs_type_status",
        "background_jobs",
        ["type", "status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_background_jobs_last_heartbeat_at",
        "background_jobs",
        ["last_heartbeat_at"],
        schema=SCHEMA,
    )

    op.execute(
        f'CREATE TRIGGER trg_background_jobs_updated_at '
        f'BEFORE UPDATE ON "{SCHEMA}".background_jobs '
        f'FOR EACH ROW EXECUTE FUNCTION "{SCHEMA}".set_updated_at()'
    )


def downgrade() -> None:
    op.execute(
        f'DROP TRIGGER IF EXISTS trg_background_jobs_updated_at ON "{SCHEMA}".background_jobs'
    )
    op.drop_index(
        "ix_background_jobs_last_heartbeat_at",
        table_name="background_jobs",
        schema=SCHEMA,
    )
    op.drop_index("ix_background_jobs_type_status", table_name="background_jobs", schema=SCHEMA)
    op.drop_index("ix_background_jobs_status", table_name="background_jobs", schema=SCHEMA)
    op.drop_index("ix_background_jobs_created_by", table_name="background_jobs", schema=SCHEMA)
    op.drop_table("background_jobs", schema=SCHEMA)

    op.execute(
        f'DROP TRIGGER IF EXISTS trg_product_links_updated_at ON "{SCHEMA}".product_links'
    )
    op.drop_index("ix_product_links_store_id", table_name="product_links", schema=SCHEMA)
    op.drop_index(
        "ix_product_links_integration_id",
        table_name="product_links",
        schema=SCHEMA,
    )
    op.drop_index("ix_product_links_product_id", table_name="product_links", schema=SCHEMA)
    op.drop_index("uq_product_links_identity", table_name="product_links", schema=SCHEMA)
    op.drop_table("product_links", schema=SCHEMA)

    op.execute(f'DROP TRIGGER IF EXISTS trg_products_updated_at ON "{SCHEMA}".products')
    op.drop_index("ix_products_bling_product_id", table_name="products", schema=SCHEMA)
    op.drop_index("ix_products_integration_id", table_name="products", schema=SCHEMA)
    op.drop_index("ix_products_user_id", table_name="products", schema=SCHEMA)
    op.drop_table("products", schema=SCHEMA)

    op.execute(f'DROP TYPE IF EXISTS "{SCHEMA}".background_job_status')
    op.execute(f'DROP TYPE IF EXISTS "{SCHEMA}".background_job_type')
    op.execute(f'DROP TYPE IF EXISTS "{SCHEMA}".link_sync_status')
