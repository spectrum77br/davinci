"""atendimento_canais.auto_continuacao_desde — a resposta automática da continuação

Eduardo, 28/10/2026: os Reels de microdrama da @uranyx_br saem em série (12h e
19h) e os seguidores comentam "Continuação"/"Continua"/"parte 2" — ninguém
responde. Ele pediu a resposta automática, em PÚBLICO, com o horário do
próximo episódio, só no Instagram (`services/atendimento/redes_continuacao.py`).

A conta segue em `observar` (ou `humano`) para todo o resto: este interruptor
libera SÓ essa resposta. NULL = desligado; preenchido = ligado desde então (o
motor só responde comentário feito DEPOIS — a fila velha não vira rajada de
respostas). Nasce desligado em todas as contas: quem liga é o admin, na aba
Lojas, conta por conta — nunca o deploy.

Revision ID: 0436_atendimento_canal_auto_continuacao
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0436_atendimento_canal_auto_continuacao"
down_revision: str | None = "0435_marca_emails_sac_admin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "atendimento_canais",
        sa.Column("auto_continuacao_desde", sa.DateTime(timezone=True), nullable=True),
        schema="davinci",
    )


def downgrade() -> None:
    op.drop_column("atendimento_canais", "auto_continuacao_desde", schema="davinci")
