# ruff: noqa: E501
"""Devolução: link do vídeo + Golpe sem sub-motivo — Eduardo, 2026-09-04.

"precisa adicionar o campo da câmera logo após o link abertura, e opção tbm de
colocar o link do vídeo; situação golpe é pacote vazio, produto diferente usa o
status item incorreto". A API do ML não aceita vídeo como anexo, então o link
entra no texto do chamado. `motivo_ml` (sub-motivo de Golpe, criado hoje na
0243) sai: Golpe → SRF5 fixo e "Item Incorreto" → SRF4.

Revision ID: 0244_devolucao_video_url
Revises: 0243_devolucao_anexo_chamado_ml
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0244_devolucao_video_url"
down_revision: str | None = "0243_devolucao_anexo_chamado_ml"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.add_column("devolutions", sa.Column("video_url", sa.Text(), nullable=True))
    # `motivo_ml` (0243) fica no banco sem uso — derrubar depois que o código
    # sem ele estiver no ar (evita quebrar a versão anterior durante o build).


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.drop_column("devolutions", "video_url")
