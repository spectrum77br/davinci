"""nf_etiqueta_arquivo: DANFE da NF 100% que vai DENTRO DA CAIXA

Eduardo (28/09/2026): os pedidos Correios seguem com a NF de 1% (a de sempre,
que viaja com a etiqueta) e passam a levar TAMBÉM a NF de 100% (produto), que
vai dentro da caixa. As duas saem na impressão, a de 1% primeiro; a tela avisa
qual é a da caixa ("na hora de imprimir não pode aparecer aquela frase", o
aviso é só na tela). Guardada à parte da `nf_pdf` (1%) pra a tela saber quais
pedidos têm a segunda nota — emendar no mesmo PDF escondia isso.

Revision ID: 0334_nf_caixa
Revises: 0333_vinculos_mortos
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0334_nf_caixa"
down_revision: str | None = "0333_vinculos_mortos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column("nf_etiqueta_arquivo", sa.Column("nf_caixa_pdf", sa.LargeBinary(), nullable=True), schema=SCHEMA)
    op.add_column(
        "nf_etiqueta_arquivo", sa.Column("nf_caixa_size_bytes", sa.BigInteger(), nullable=True), schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_column("nf_etiqueta_arquivo", "nf_caixa_size_bytes", schema=SCHEMA)
    op.drop_column("nf_etiqueta_arquivo", "nf_caixa_pdf", schema=SCHEMA)
