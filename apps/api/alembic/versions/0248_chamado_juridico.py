# ruff: noqa: E501
"""Chamados: encaminhar ao JURÍDICO — Eduardo, 2026-09-04.

"em chamados, vamos colocar uma opção de encaminhar para o jurídico, que vamos
encaminhar toda a mensagem com as fotos … coloque o botão de informar, vamos
criar tbm outra aba chamada jurídico, que vão ficar salvos todos que nós
mandarmos para o jurídico".

O Threema do "Informar" só manda texto → o aviso vai com um LINK do dossiê
(histórico completo + fotos) aberto por token secreto (`juridico_token`).
Os campos abaixo marcam quem/quando encaminhou e alimentam a aba Jurídico.

Revision ID: 0248_chamado_juridico
Revises: 0247_devolucao_remove_video_url
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0248_chamado_juridico"
down_revision: str | None = "0247_devolucao_remove_video_url"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.add_column("chamados", sa.Column("juridico_enviado_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "chamados",
        sa.Column(
            "juridico_enviado_por",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("chamados", sa.Column("juridico_obs", sa.Text(), nullable=True))
    op.add_column("chamados", sa.Column("juridico_token", sa.Text(), nullable=True))
    op.add_column("chamados", sa.Column("juridico_destinatarios", sa.Text(), nullable=True))
    op.create_index("ix_chamados_juridico_token", "chamados", ["juridico_token"], schema=SCHEMA)


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.drop_index("ix_chamados_juridico_token", table_name="chamados", schema=SCHEMA)
    for col in ("juridico_destinatarios", "juridico_token", "juridico_obs", "juridico_enviado_por", "juridico_enviado_at"):
        op.drop_column("chamados", col)
