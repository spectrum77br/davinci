"""chamado_mensagem.excluida_at/excluida_por: lixeirinha no histórico do chamado

Vinicius (25/09/2026, chamado 260914016HQB8XN / pedido 294571): "quero excluir uma
mensagem do histórico". Excluir de verdade quebraria o acompanhamento — a varredura
só grava a fala da plataforma que ainda não está no histórico (voltaria na passada
seguinte) e marcas de sistema seguram respostas automáticas. Então "excluir" esconde:
some da tela e da leitura da IA de Chamado; status da aba e robôs continuam contando.

  excluida_at   quando alguém escondeu (NULL = visível)
  excluida_por  quem

Revision ID: 0329_chamado_mensagem_excluida
Revises: 0328_criativos_fila_posicao
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0329_chamado_mensagem_excluida"
down_revision: str | None = "0328_criativos_fila_posicao"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.add_column(
        "chamado_mensagem",
        sa.Column("excluida_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "chamado_mensagem", sa.Column("excluida_por", sa.Text(), nullable=True), schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_column("chamado_mensagem", "excluida_por", schema=SCHEMA)
    op.drop_column("chamado_mensagem", "excluida_at", schema=SCHEMA)
