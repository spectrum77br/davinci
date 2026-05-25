# ruff: noqa: E501
"""import_products.tsa: bool → int (nullable).

The operator's planilha uses TSA as a count (1, 2, 3 cadeados, NULL =
no TSA). The v1 column was a NOT NULL boolean defaulting to false,
which collapsed the distinction between "no TSA" and "1 cadeado".

Migration:
  * drop NOT NULL + server_default
  * ALTER TYPE INTEGER with USING (true → 1, false → NULL)

Reversible — downgrade restores the boolean (any value >= 1 → true).

Revision ID: 0090_import_products_tsa_int
Revises: 0089_merge_importacao_transporte
Create Date: 2026-05-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0090_import_products_tsa_int"
down_revision: str | None = "0089_merge_importacao_transporte"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f"""
        ALTER TABLE {SCHEMA}.import_products
            ALTER COLUMN tsa DROP DEFAULT,
            ALTER COLUMN tsa DROP NOT NULL,
            ALTER COLUMN tsa TYPE INTEGER USING (
                CASE WHEN tsa THEN 1 ELSE NULL END
            )
    """)


def downgrade() -> None:
    op.execute(f"""
        ALTER TABLE {SCHEMA}.import_products
            ALTER COLUMN tsa TYPE BOOLEAN USING (
                CASE WHEN tsa IS NOT NULL AND tsa >= 1 THEN TRUE ELSE FALSE END
            ),
            ALTER COLUMN tsa SET NOT NULL,
            ALTER COLUMN tsa SET DEFAULT FALSE
    """)
