"""Ouvidoria › Robôs — a lixeira do painel: robô arquivado some da lista

Vinicius, 22/09/2026: "depois do Rodar agora precisaria incluir uma lixeira
pra tirar o robô desse painel — não pra excluir o robô definitivamente".

Arquivar é só sobre a VISTA: a linha continua em `ouvidoria_robos`, o robô
continua rodando na cadência dele, registrando ocorrência e avisando no
Threema — ele decidiu assim, com o modo intocado. Some da lista da aba Robôs
e volta pelo atalho "N arquivados".

Guarda quem arquivou junto com quando, igual ao `modo_alterado_por`: o painel
é de várias mãos e sumir uma linha sem assinatura vira mistério.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0307_ouvidoria_robo_arquivado"
down_revision: str | None = "0306_roteiro_versao_e_requisicao_de_personagem"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "ouvidoria_robos",
        sa.Column("arquivado_em", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "ouvidoria_robos",
        sa.Column("arquivado_por", sa.String(length=120), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("ouvidoria_robos", "arquivado_por", schema=SCHEMA)
    op.drop_column("ouvidoria_robos", "arquivado_em", schema=SCHEMA)
