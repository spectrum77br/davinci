# ruff: noqa: E501
"""Logística: marca de qual rastreio já foi registrado no 17track — Eduardo, 2026-09-04.

"em status o porque nao esta atualizando, rastreio e localização de correios nao
esta atualizando, acho que utilizamos 17track" + "isso e em logistica, sempre
que mudar, precisa atualizar em tempo real tbm".

Até aqui NINGUÉM registrava no 17track os rastreios de ENVIO da Logística: o
`logistica_track.register` só era chamado pelo sync de DEVOLUÇÃO e por um
endpoint manual que nem tem botão na tela. Sem registro o 17track não busca nos
Correios e nunca empurra evento — a coluna Localização ficava eternamente com o
proxy do ML. Comprovado no 291809 (AD828496989BR): o 17track respondeu "does not
register, please register first", e ZERO linhas da tabela tinham localização no
formato dele.

Estas colunas são a memória do job novo (`logistica_track_sync`): guardam QUAL
número já foi registrado, pra não gastar quota repetindo, e pra re-registrar
sozinho quando o marketplace troca o código de rastreio.

Revision ID: 0245_logistica_rastreio_17track
Revises: 0244_devolucao_video_url
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0245_logistica_rastreio_17track"
down_revision: str | None = "0244_devolucao_video_url"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "logistica",
        sa.Column("rastreio_17track", sa.Text(), nullable=True),
        schema="davinci",
    )
    op.add_column(
        "logistica",
        sa.Column("rastreio_17track_at", sa.DateTime(timezone=True), nullable=True),
        schema="davinci",
    )
    op.add_column(
        "logistica",
        sa.Column("localizacao_at", sa.DateTime(timezone=True), nullable=True),
        schema="davinci",
    )


def downgrade() -> None:
    op.drop_column("logistica", "localizacao_at", schema="davinci")
    op.drop_column("logistica", "rastreio_17track_at", schema="davinci")
    op.drop_column("logistica", "rastreio_17track", schema="davinci")
