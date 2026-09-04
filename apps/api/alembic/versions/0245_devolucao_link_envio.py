# ruff: noqa: E501
"""Devolução: "Link de envio" (prova da expedição) — Eduardo, 2026-09-04.

"antes de link de abertura coloque uma opção, link de envio, com uma trava:
mala e eletro é obrigatória, desde que esteja nos motivos que abrem chamado".
É o link das fotos/vídeo feitos na expedição do pedido — a prova que Shopee e
TikTok pedem pra contestar "pacote vazio"/"item errado"; entra no texto do
chamado automático.

Revision ID: 0245_devolucao_link_envio
Revises: 0244_devolucao_video_url
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0245_devolucao_link_envio"
down_revision: str | None = "0244_devolucao_video_url"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.add_column("devolutions", sa.Column("link_envio", sa.Text(), nullable=True))


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.drop_column("devolutions", "link_envio")
