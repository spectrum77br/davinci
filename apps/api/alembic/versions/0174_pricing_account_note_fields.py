# ruff: noqa: E501
"""pricing_accounts: campos livres desconto/afiliado/ads/cupom por loja

A tabela de preço mostrava 3 campos genéricos de observação (obs1/obs2/obs3)
embaixo do nome de cada loja. Trocamos por 5 campos rotulados: desconto,
afiliado, ads, cupom e obs. `observation` (obs1) é reusado como "obs"; as 4
colunas abaixo são novas. obs2/obs3 permanecem no banco (dados preservados),
apenas somem da UI.

Revision ID: 0174_pricing_account_note_fields
Revises: 0173_store_info_integration_archived_at
Create Date: 2026-07-07
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0174_pricing_account_note_fields"
down_revision: str | None = "0173_store_info_integration_archived_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

_COLS = ("discount", "affiliate", "ads", "coupon")


def upgrade() -> None:
    for col in _COLS:
        op.add_column(
            "pricing_accounts",
            sa.Column(col, sa.Text(), nullable=True),
            schema=SCHEMA,
        )


def downgrade() -> None:
    for col in reversed(_COLS):
        op.drop_column("pricing_accounts", col, schema=SCHEMA)
