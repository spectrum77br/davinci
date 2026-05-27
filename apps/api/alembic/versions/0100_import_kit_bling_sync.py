# ruff: noqa: E501
"""import_kit_marks: add bling sync state columns.

Quando o operador marca "x" na matriz Kit, o sistema enfileira um job
ARQ que cria um produto composto (formato="E") no Bling. Esses 5
campos guardam o estado de sincronização por mark:

  * bling_product_id      — id retornado pelo Bling (NULL até criar)
  * bling_sync_status     — 'pending' | 'sent' | 'error' | NULL
  * bling_sync_error      — última mensagem de erro
  * bling_sync_attempted_at — última tentativa (success ou error)
  * bling_sync_done_at    — quando entrou em 'sent'

Idempotência: o worker pula se bling_product_id já estiver não-nulo.

Revision ID: 0100_import_kit_bling_sync
Revises: 0099_import_kit_seed
Create Date: 2026-05-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0100_import_kit_bling_sync"
down_revision: str | None = "0099_import_kit_seed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "import_kit_marks",
        sa.Column("bling_product_id", sa.BigInteger(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "import_kit_marks",
        sa.Column("bling_sync_status", sa.String(20), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "import_kit_marks",
        sa.Column("bling_sync_error", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "import_kit_marks",
        sa.Column("bling_sync_attempted_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "import_kit_marks",
        sa.Column("bling_sync_done_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    # Index pra encontrar marks pending/error rapidinho (pra worker
    # retry sweep ou queries de status agregado).
    op.create_index(
        "ix_import_kit_marks_sync_status",
        "import_kit_marks",
        ["bling_sync_status"],
        schema=SCHEMA,
        postgresql_where=sa.text("bling_sync_status IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_import_kit_marks_sync_status",
        table_name="import_kit_marks",
        schema=SCHEMA,
    )
    op.drop_column("import_kit_marks", "bling_sync_done_at", schema=SCHEMA)
    op.drop_column("import_kit_marks", "bling_sync_attempted_at", schema=SCHEMA)
    op.drop_column("import_kit_marks", "bling_sync_error", schema=SCHEMA)
    op.drop_column("import_kit_marks", "bling_sync_status", schema=SCHEMA)
    op.drop_column("import_kit_marks", "bling_product_id", schema=SCHEMA)
