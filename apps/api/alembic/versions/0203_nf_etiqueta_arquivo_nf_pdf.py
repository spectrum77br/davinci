# ruff: noqa: E501
"""nf_etiqueta_arquivo.nf_pdf: DANFE do Bling pra junção correios/ML

No fluxo correios (ML) a etiqueta não leva declaração — leva a NF junto. A NF
vem do Bling ("Gerar PDF DANFE"), é capturada pela marionete e gravada aqui;
quando presente, o botão "Imprimir Etiqueta" serve etiqueta + NF juntadas.
NULL = fluxo agência (só etiqueta).

Revision ID: 0203_nf_etiqueta_arquivo_nf_pdf
Revises: 0202_nf_command_ads_power
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0203_nf_etiqueta_arquivo_nf_pdf"
down_revision: str | None = "0202_nf_command_ads_power"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "nf_etiqueta_arquivo",
        sa.Column("nf_pdf", sa.LargeBinary(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "nf_etiqueta_arquivo",
        sa.Column("nf_size_bytes", sa.BigInteger(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("nf_etiqueta_arquivo", "nf_size_bytes", schema=SCHEMA)
    op.drop_column("nf_etiqueta_arquivo", "nf_pdf", schema=SCHEMA)
