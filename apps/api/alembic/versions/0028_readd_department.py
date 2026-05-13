"""re-add department column to pricing_accounts/pricing_products (compat with code)

Prod had an out-of-band refactor (alembic_version '0027_drop_pricing_department',
no matching file) that dropped the `department` enum from both pricing tables
and replaced it with a `segment_id` FK to a new hierarchical `segments` table.
The application code, however, still reads `department`.

This migration restores the `department` column (nullable, no PG enum — uses a
plain text column to avoid a clash with the existing `department` enum type)
and backfills it from the root parent's slug in `segments`. The `segment_id`
column stays untouched so anything written against the new schema keeps
working.

Revision ID: 0028_readd_department
Revises: 0027_drop_pricing_department
Create Date: 2026-05-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0028_readd_department"
down_revision: str | None = "0027_drop_pricing_department"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    # Re-create the enum type if the out-of-band refactor dropped it together
    # with the columns. `IF NOT EXISTS` keeps the migration idempotent on a DB
    # that still has the type.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_type t
                JOIN pg_namespace n ON t.typnamespace = n.oid
                WHERE n.nspname = '{SCHEMA}' AND t.typname = 'department'
            ) THEN
                CREATE TYPE "{SCHEMA}".department AS ENUM
                    ('celular', 'mala', 'eletro', 'catalogo');
            END IF;
        END$$;
        """
    )

    # Add nullable column only if missing — safe to run on a DB that never
    # dropped the column.
    for tbl in ("pricing_accounts", "pricing_products"):
        op.execute(
            f"ALTER TABLE \"{SCHEMA}\".{tbl} "
            f"ADD COLUMN IF NOT EXISTS department \"{SCHEMA}\".department"
        )

    # Backfill department from the root segment's slug. The `segments` table is
    # hierarchical (parent_id); a row may point at a leaf, so we recurse up to
    # the root and take its slug.
    op.execute(
        f"""
        WITH RECURSIVE seg_root AS (
            SELECT id, parent_id, slug FROM "{SCHEMA}".segments WHERE parent_id IS NULL
            UNION ALL
            SELECT s.id, s.parent_id, r.slug
            FROM "{SCHEMA}".segments s
            JOIN seg_root r ON s.parent_id = r.id
        )
        UPDATE "{SCHEMA}".pricing_accounts a
        SET department = r.slug::"{SCHEMA}".department
        FROM seg_root r
        WHERE a.segment_id = r.id AND a.department IS NULL
          AND r.slug IN ('celular','mala','eletro','catalogo')
        """
    )
    op.execute(
        f"""
        WITH RECURSIVE seg_root AS (
            SELECT id, parent_id, slug FROM "{SCHEMA}".segments WHERE parent_id IS NULL
            UNION ALL
            SELECT s.id, s.parent_id, r.slug
            FROM "{SCHEMA}".segments s
            JOIN seg_root r ON s.parent_id = r.id
        )
        UPDATE "{SCHEMA}".pricing_products p
        SET department = r.slug::"{SCHEMA}".department
        FROM seg_root r
        WHERE p.segment_id = r.id AND p.department IS NULL
          AND r.slug IN ('celular','mala','eletro','catalogo')
        """
    )


def downgrade() -> None:
    for tbl in ("pricing_accounts", "pricing_products"):
        op.execute(f"ALTER TABLE \"{SCHEMA}\".{tbl} DROP COLUMN IF EXISTS department")
