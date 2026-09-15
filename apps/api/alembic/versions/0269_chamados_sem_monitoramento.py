"""chamados.monitoramento — sai do banco: o robô acompanha TODOS os chamados

Eduardo, 15/09/2026: "o robô precisa acompanhar todos os chamados, então
podemos deixar sempre ligado e tirar do visual". O cron dos Chamados passa a
fechar sozinho todo chamado aberto de canal API do ML cujo claim o ML encerrou,
sem olhar flag nenhuma — a coluna virou letra morta e cai aqui. (Antes, um
chamado criado à mão nascia com o sim/não desligado e ficava fora do
acompanhamento sem ninguém perceber.)
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0269_chamados_sem_monitoramento"
down_revision: str | None = "0268_prioridade_estoque_movimentos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.drop_column("chamados", "monitoramento", schema=SCHEMA)


def downgrade() -> None:
    op.add_column(
        "chamados",
        sa.Column("monitoramento", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        schema=SCHEMA,
    )
