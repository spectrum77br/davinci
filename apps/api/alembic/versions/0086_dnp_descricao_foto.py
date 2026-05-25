# ruff: noqa: E501
"""DNP — collapse voltagem/cor/material/tamanho/potencia into `descricao` + add `foto_url`.

Before: 5 separate attribute columns operators had to fill row-by-row.
        The planilha layout was cramped and most rows only used 2-3 of them.
After:  Single free-text `descricao` column (operator concatenates whatever
        attributes matter for that product) plus a `foto_url` column that
        stores the LocalStorage path of the product photo. The Vue page
        renders a thumbnail with click-to-enlarge lightbox.

Backfill: any non-empty value from the old 5 columns is concatenated into
the new `descricao` with " | " separators, preserving the order
voltagem → cor → material → tamanho → potencia. Empty/NULL values are
skipped, so a row that only had `cor='preto'` lands as `descricao='preto'`.

Revision ID: 0086_dnp_descricao_foto
Revises: 0085_dnp
Create Date: 2026-05-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0086_dnp_descricao_foto"
down_revision: str | None = "0085_dnp"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    # 1. Add the new columns (nullable so backfill can run).
    op.add_column(
        "dnp_produtos",
        sa.Column("descricao", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "dnp_produtos",
        sa.Column("foto_url", sa.Text(), nullable=True),
        schema=SCHEMA,
    )

    # 2. Backfill descricao = concat_ws(' | ', NULLIF(voltagem,''), NULLIF(cor,''), …).
    #    NULLIF turns empty strings into NULLs so concat_ws skips them
    #    (concat_ws drops NULLs but NOT empty strings).
    op.execute(
        f"""
        UPDATE {SCHEMA}.dnp_produtos
        SET descricao = NULLIF(
            concat_ws(
                ' | ',
                NULLIF(voltagem, ''),
                NULLIF(cor, ''),
                NULLIF(material, ''),
                NULLIF(tamanho, ''),
                NULLIF(potencia, '')
            ),
            ''
        )
        """
    )

    # 3. Drop the old per-attribute columns.
    for col in ("voltagem", "cor", "material", "tamanho", "potencia"):
        op.drop_column("dnp_produtos", col, schema=SCHEMA)


def downgrade() -> None:
    # Re-create the old columns, leave them NULL (we don't split descricao
    # back out — that would require a parser and would corrupt manually-
    # written descricao values).
    for col in ("voltagem", "cor", "material", "tamanho", "potencia"):
        op.add_column(
            "dnp_produtos",
            sa.Column(col, sa.Text(), nullable=True),
            schema=SCHEMA,
        )
    op.drop_column("dnp_produtos", "foto_url", schema=SCHEMA)
    op.drop_column("dnp_produtos", "descricao", schema=SCHEMA)
