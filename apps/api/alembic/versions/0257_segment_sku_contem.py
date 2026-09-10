# ruff: noqa: E501
"""Condição Especial: SKU passa de "começa com" para "contém" — coluna renomeada.

Decisão de 10/09/2026 ("pode ser o contém"): a condição por SKU casa quando o
SKU CONTÉM o texto, como a condição por nome. A coluna `sku_prefixo` (0256)
vira `sku_contem` só pra o nome não mentir; dado e CHECKs continuam iguais
(o CHECK referencia a coluna por posição, acompanha o rename).

Revision ID: 0257_segment_sku_contem
Revises: 0256_segment_condicao_especial
Create Date: 2026-09-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0257_segment_sku_contem"
down_revision: str | None = "0256_segment_condicao_especial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
TABLE = "segment_special_dates"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.alter_column(TABLE, "sku_prefixo", new_column_name="sku_contem")


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.alter_column(TABLE, "sku_contem", new_column_name="sku_prefixo")
