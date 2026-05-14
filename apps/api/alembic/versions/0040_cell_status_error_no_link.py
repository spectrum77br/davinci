"""Add 'error' and 'no_link' to cell_status enum

SSH push semantics: a failed push writes 'error' (API rejected) or
'no_link' (resolver found 0 product_links) to pricing_overrides.cell_status,
so the UI can show a "NA"/"SV" button on that specific cell. Successful
push clears the status back to 'auto'.

Revision ID: 0040_cell_status_error_no_link
Revises: 0039_margens_plataforma
Create Date: 2026-05-14
"""

from __future__ import annotations

from alembic import op

revision = "0040_cell_status_error_no_link"
down_revision = "0039_margens_plataforma"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PG requires ADD VALUE to run outside a transaction; alembic
    # opens one by default, so commit first.
    op.execute("COMMIT")
    op.execute("ALTER TYPE cell_status ADD VALUE IF NOT EXISTS 'error'")
    op.execute("ALTER TYPE cell_status ADD VALUE IF NOT EXISTS 'no_link'")


def downgrade() -> None:
    # Postgres has no DROP VALUE; downgrade would require recreating the
    # enum. Treat this as a forward-only schema change.
    pass
