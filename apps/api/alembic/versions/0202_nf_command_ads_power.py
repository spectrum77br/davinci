# ruff: noqa: E501
"""nf_command.ads_power: AdsPower do comando (etiqueta ML usa o perfil do cadastro Etiqueta)

Um comando de etiqueta ML importa/imprime no Upseller pela conta do cadastro
Etiqueta (perfil AdsPower proprio), NAO do faturador (que emitiu a NF no Bling).
Guarda esse perfil por-comando; NULL cai no ads_power do faturador (fluxo antigo).

Revision ID: 0202_nf_command_ads_power
Revises: 0201_nf_etiqueta_arquivo
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0202_nf_command_ads_power"
down_revision: str | None = "0201_nf_etiqueta_arquivo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "nf_command",
        sa.Column("ads_power", sa.Text(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("nf_command", "ads_power", schema=SCHEMA)
