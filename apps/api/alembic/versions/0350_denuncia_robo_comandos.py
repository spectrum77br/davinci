"""denuncia_robo_comandos — botões da aba Robô (ligar/desligar a rotina, rodar um passo)

Vinicius, 01/10/2026: "ao invés da rodada começar agendada, disparamos o
passo 1, acompanhamos… depois o passo 2" e "um botão no DaVinci para ligar
rotinas automáticas e desligar". O DaVinci não alcança o Mac mini, então o
botão grava um comando aqui; a janela do sistema no mini pergunta a cada 5 s
(`GET /api/denuncia/sync/robo/comandos`), executa (despertador.json /
gatilho do passo) e devolve o resultado.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0350_denuncia_robo_comandos"
down_revision: str | None = "0349_denuncia_prova_pedida"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "denuncia_robo_comandos",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("dados", postgresql.JSONB(), nullable=False),
        sa.Column("pedido_por", sa.Text()),
        sa.Column(
            "pedido_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("entregue_em", sa.DateTime(timezone=True)),
        sa.Column("ok", sa.Boolean()),
        sa.Column("resultado", sa.Text()),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_denuncia_robo_comandos_pendentes",
        "denuncia_robo_comandos",
        ["pedido_em"],
        schema=SCHEMA,
        postgresql_where=sa.text("entregue_em IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_denuncia_robo_comandos_pendentes", "denuncia_robo_comandos", schema=SCHEMA)
    op.drop_table("denuncia_robo_comandos", schema=SCHEMA)
