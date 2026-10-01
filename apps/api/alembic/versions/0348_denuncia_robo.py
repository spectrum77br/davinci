"""denuncia_robo_status — o que o robô do Mac mini está fazendo agora

Vinicius, 01/10/2026: "como eu vou saber em que passo ele está, o que está
fazendo". O robô da fiscalização (agente_varredura.py, no Mac mini da Makisa)
já monta a cada 60 s um resumo do estado dele (`status_mac.py`: agente vivo,
filas, tarefas da rodada do dia, contas, problemas). O mini passa a mandar
esse resumo pra cá (`POST /api/denuncia/sync/robo`) e a aba Robô de
Ouvidoria › Denúncia mostra.

Uma linha por remetente (só o último resumo importa; o histórico continua
nos logs do mini).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0348_denuncia_robo"
down_revision: str | None = "0347_atendimento_robo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "denuncia_robo_status",
        sa.Column("remetente", sa.Text(), primary_key=True),
        sa.Column("dados", postgresql.JSONB(), nullable=False),
        sa.Column(
            "recebido_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("denuncia_robo_status", schema=SCHEMA)
