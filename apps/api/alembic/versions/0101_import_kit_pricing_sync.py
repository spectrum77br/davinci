# ruff: noqa: E501
"""import_kit_marks: add pricing_product sync state.

Fase 3 da aba Kit — depois que o Bling create da fase 2 termina com
sucesso, criar/atualizar pricing_product em /pricing/tabela. Estes
campos guardam o estado dessa segunda sync:

  * pricing_product_id      — FK pra pricing_products (NULL até criar)
  * pricing_sync_status     — 'pending' | 'sent' | 'error' | NULL
  * pricing_sync_error      — última mensagem de erro
  * pricing_sync_done_at    — quando entrou em 'sent'

Idempotência: worker pula se pricing_product_id já preenchido.

FK ON DELETE SET NULL — operador pode deletar pricing_product no /pricing
sem corromper a mark; ela só perde o link.

Revision ID: 0101_import_kit_pricing_sync
Revises: 0100_import_kit_bling_sync
Create Date: 2026-05-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0101_import_kit_pricing_sync"
down_revision: str | None = "0100_import_kit_bling_sync"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "import_kit_marks",
        sa.Column("pricing_product_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "import_kit_marks",
        sa.Column("pricing_sync_status", sa.String(20), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "import_kit_marks",
        sa.Column("pricing_sync_error", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "import_kit_marks",
        sa.Column("pricing_sync_done_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_import_kit_marks_pricing_product",
        source_table="import_kit_marks",
        referent_table="pricing_products",
        local_cols=["pricing_product_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_import_kit_marks_pricing_product",
        "import_kit_marks",
        type_="foreignkey",
        schema=SCHEMA,
    )
    op.drop_column("import_kit_marks", "pricing_sync_done_at", schema=SCHEMA)
    op.drop_column("import_kit_marks", "pricing_sync_error", schema=SCHEMA)
    op.drop_column("import_kit_marks", "pricing_sync_status", schema=SCHEMA)
    op.drop_column("import_kit_marks", "pricing_product_id", schema=SCHEMA)
