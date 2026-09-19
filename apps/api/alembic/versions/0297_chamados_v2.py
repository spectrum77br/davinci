"""Chamados v2 — chamados.valor_sugerido e chamado_mensagem.tentativas

Vinicius, 19/09/2026: "primeiro o robô; gente só quando o robô desiste" e
NADA fecha chamado sozinho. Até aqui o acompanhamento das devoluções, o cron
dos claims do ML, o monitor do Tuta e o cérebro marcavam `resolvido` por
conta própria — e o resultado (lucro/prejuízo) entrava sem ninguém conferir.
Agora a plataforma/robô só põem o chamado no estado "Encerrado" (status
oficial final + evento) e SUGEREM o valor; uma pessoa conclui pelo
`/resolver`.

- `chamados.valor_sugerido` NUMERIC(12,2) NULL: sugestão de resultado (robô
  ou plataforma). A pessoa confirma ao fechar → `valor_recuperado`.
- `chamado_mensagem.tentativas` INTEGER NOT NULL DEFAULT 0: quantas vezes o
  robô já tentou enviar. Falha volta pra fila até 3 tentativas
  (`MAX_TENTATIVAS_ROBO`); só então fica `falhou` e vira Análise Humano.
- Tipo novo de mensagem `instrucao` (recado de pessoa pro robô) — `tipo` é
  texto livre no banco, sem constraint; nada a migrar.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.config import get_settings

revision: str = "0297_chamados_v2"
down_revision: str | None = "0296_devolucao_destino_auto"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = get_settings().database_schema


def upgrade() -> None:
    op.add_column(
        "chamados",
        sa.Column("valor_sugerido", sa.Numeric(12, 2), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "chamado_mensagem",
        sa.Column("tentativas", sa.Integer(), nullable=False, server_default="0"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("chamado_mensagem", "tentativas", schema=SCHEMA)
    op.drop_column("chamados", "valor_sugerido", schema=SCHEMA)
