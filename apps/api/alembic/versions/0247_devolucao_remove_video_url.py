# ruff: noqa: E501
"""Devolução: tira o campo "Link vídeo" — Eduardo, 2026-09-04.

"pode remover, o link de video da aba devoluções". O campo tinha sido criado
na 0244 no mesmo dia e nunca foi usado: 0 preenchidos em 1925 devoluções.
A foto/vídeo anexado continua (botão da câmera) e o "Link envio" também; o que
sai é só a coluna de digitar o link do vídeo e a linha "Vídeo da devolução:" que
ele acrescentava no texto do chamado automático.

Revision ID: 0247_devolucao_remove_video_url
Revises: 0246_logistica_rastreio_17track
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0247_devolucao_remove_video_url"
down_revision: str | None = "0246_logistica_rastreio_17track"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("devolutions", "video_url", schema="davinci")


def downgrade() -> None:
    op.add_column(
        "devolutions",
        sa.Column("video_url", sa.Text(), nullable=True),
        schema="davinci",
    )
