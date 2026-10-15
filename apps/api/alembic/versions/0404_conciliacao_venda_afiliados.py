"""Conciliação — coluna venda_afiliados em conciliacao_pedidos (15/10).

Vinicius: "tarifa é tarifa, aquela porcentagem sempre igual; afiliado, cupom, tudo
que for assim entra em Extras". Shopee e TikTok passam a guardar a comissão de
afiliados à parte (ML fica como está). Preenche no próximo recálculo.

Revision ID: 0404_conciliacao_venda_afiliados
Revises: 0403_conciliacao_tiktok_dia_do_banco
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0404_conciliacao_venda_afiliados"
down_revision: str | None = "0403_conciliacao_tiktok_dia_do_banco"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conciliacao_pedidos",
        sa.Column("venda_afiliados", sa.Numeric(14, 2), nullable=True),
        schema="davinci",
    )


def downgrade() -> None:
    op.drop_column("conciliacao_pedidos", "venda_afiliados", schema="davinci")
