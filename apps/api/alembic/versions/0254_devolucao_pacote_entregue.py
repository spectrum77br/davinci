# ruff: noqa: E501
"""Devoluções: dia em que o pacote de VOLTA chegou ao vendedor.

Eduardo, 10/09/2026: "291516 esse pedido esta entregue e a ultima localizacao
nao esta alterada, preciso que isso fique redondo 100%". A Shopee já sabia — o
detalhe da devolução traz `reverse_logistics_status = LOGISTICS_DELIVERY_DONE`
—, mas o DaVinci só lia a LISTA, que não traz esse campo. Sem isso a tela caía
no status do CASO ("Devolução em processamento") e parecia congelada.

Revision ID: 0254_devolucao_pacote_entregue
Revises: 0253_logistica_auto_status
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0254_devolucao_pacote_entregue"
down_revision: str | None = "0253_logistica_auto_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.add_column(
        "devolucao_rastreio",
        sa.Column("pacote_entregue_em", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.drop_column("devolucao_rastreio", "pacote_entregue_em")
