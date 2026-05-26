# ruff: noqa: E501
"""import_products: add bling_sync_status + bling_sync_marked_at columns.

Tracks the operator intent to send a newly-created mala to the Bling
account. The actual Bling write integration doesn't exist yet — `BlingClient`
only supports stock/price/situacao updates, not product creation — so the
"Enviar pro Bling" button just flips this column to 'pending'. A future
worker will pick up pending rows and call the real Bling endpoint, leaving
them at 'sent' (or 'error').

Status values (free-form varchar, not an enum, so we can add states like
'queued' later without a migration):
  * NULL       — never marked for sync (default for legacy rows)
  * 'pending'  — operator clicked "Enviar pro Bling", awaiting worker
  * 'sent'     — successfully created in Bling
  * 'error'    — last sync attempt failed (see obs for details)

Revision ID: 0091_import_products_bling_sync
Revises: 0090_import_products_tsa_int
Create Date: 2026-05-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0091_import_products_bling_sync"
down_revision: str | None = "0090_import_products_tsa_int"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f"""
        ALTER TABLE {SCHEMA}.import_products
            ADD COLUMN bling_sync_status VARCHAR(20),
            ADD COLUMN bling_sync_marked_at TIMESTAMPTZ
    """)


def downgrade() -> None:
    op.execute(f"""
        ALTER TABLE {SCHEMA}.import_products
            DROP COLUMN bling_sync_marked_at,
            DROP COLUMN bling_sync_status
    """)
