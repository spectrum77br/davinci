"""audit_uploads + audit_runs + audit_findings (Fase 10)

Revision ID: 0013_audit
Revises: 0012_store_info
Create Date: 2026-05-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013_audit"
down_revision: str | None = "0012_store_info"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

RUN_STATUSES = ("pending", "running", "succeeded", "failed", "cancelled")
FINDING_STATUSES = ("ok", "price_mismatch", "missing", "paused", "extra")


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')

    bind = op.get_bind()
    sa.Enum(*RUN_STATUSES, name="audit_run_status", schema=SCHEMA).create(
        bind, checkfirst=True
    )
    sa.Enum(*FINDING_STATUSES, name="audit_finding_status", schema=SCHEMA).create(
        bind, checkfirst=True
    )

    # ----------------------------------------------------------- audit_uploads
    op.create_table(
        "audit_uploads",
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
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "sheets",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_audit_uploads_user", "audit_uploads", ["user_id"], schema=SCHEMA
    )

    # ----------------------------------------------------------- audit_runs
    op.create_table(
        "audit_runs",
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
            "upload_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.audit_uploads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.background_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("sheet_name", sa.Text(), nullable=False),
        sa.Column(
            "account_map",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="{column_header: pricing_account_id}",
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                *RUN_STATUSES,
                name="audit_run_status",
                schema=SCHEMA,
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("total", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("processed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "summary",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_audit_runs_user", "audit_runs", ["user_id"], schema=SCHEMA)
    op.create_index(
        "ix_audit_runs_upload", "audit_runs", ["upload_id"], schema=SCHEMA
    )

    # --------------------------------------------------------- audit_findings
    op.create_table(
        "audit_findings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.audit_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column(
            "pricing_product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.pricing_products.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "pricing_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.pricing_accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("column_header", sa.Text(), nullable=True),
        sa.Column("expected_price", sa.Numeric(10, 2), nullable=True),
        sa.Column("actual_price", sa.Numeric(10, 2), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                *FINDING_STATUSES,
                name="audit_finding_status",
                schema=SCHEMA,
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column(
            "fixed", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("fixed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "meta",
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
        schema=SCHEMA,
    )
    op.create_index(
        "ix_audit_findings_run_status",
        "audit_findings",
        ["run_id", "status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_audit_findings_user", "audit_findings", ["user_id"], schema=SCHEMA
    )
    op.create_index(
        "ix_audit_findings_sku",
        "audit_findings",
        ["run_id", "sku"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')

    op.drop_index("ix_audit_findings_sku", table_name="audit_findings", schema=SCHEMA)
    op.drop_index(
        "ix_audit_findings_user", table_name="audit_findings", schema=SCHEMA
    )
    op.drop_index(
        "ix_audit_findings_run_status", table_name="audit_findings", schema=SCHEMA
    )
    op.drop_table("audit_findings", schema=SCHEMA)

    op.drop_index("ix_audit_runs_upload", table_name="audit_runs", schema=SCHEMA)
    op.drop_index("ix_audit_runs_user", table_name="audit_runs", schema=SCHEMA)
    op.drop_table("audit_runs", schema=SCHEMA)

    op.drop_index("ix_audit_uploads_user", table_name="audit_uploads", schema=SCHEMA)
    op.drop_table("audit_uploads", schema=SCHEMA)

    bind = op.get_bind()
    sa.Enum(name="audit_finding_status", schema=SCHEMA).drop(bind, checkfirst=True)
    sa.Enum(name="audit_run_status", schema=SCHEMA).drop(bind, checkfirst=True)
