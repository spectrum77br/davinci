"""refunds table — recovery stub for an orphan alembic stamp in prod

Revision ID: 0065_refunds
Revises: 0064_add_shein_enums
Create Date: 2026-05-20 (stub written retroactively)

Why this file exists: production's `davinci.alembic_version` was stamped
to `0065_refunds` at some point in the past, and the `davinci.refunds`
table was created in prod, but the corresponding migration file never
landed in main (lost in a rebase or branch that wasn't merged). The
table is still in use (15 columns, populated). Without this stub
alembic refuses to navigate the chain — `Can't locate revision
identified by '0065_refunds'` — and all subsequent migrations fail.

This recovery stub is idempotent:
  * On prod (table already there): `CREATE TABLE IF NOT EXISTS` is a
    no-op, so we just advance version_num.
  * On a fresh DB (no refunds table yet): creates the table matching
    the prod schema exactly.

Downgrade drops the table (matches what a real refunds migration
would have done).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0065_refunds"
down_revision: str | None = "0064_add_shein_enums"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    # Idempotent CREATE — does nothing on prod where the table is
    # already there from the pre-stamp history, but works on dev/local.
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.refunds (
            id              uuid NOT NULL,
            data            timestamptz NULL,
            pedido_bling    text NULL,
            pedido_marketplace text NULL,
            plataforma      text NULL,
            conta           text NULL,
            tipo            text NULL,
            prejuizo        double precision NULL,
            reembolso       double precision NULL,
            chamado         text NULL,
            operacao        text NULL,
            conferido       boolean NULL,
            observacao      text NULL,
            created_at      timestamptz NULL,
            updated_at      timestamptz NULL,
            CONSTRAINT pk_refunds PRIMARY KEY (id)
        )
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.refunds")
