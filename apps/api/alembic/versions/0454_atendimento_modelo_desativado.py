"""cada equipe desativa as respostas padrão só para ela

Eduardo, 05/11/2026: "os 3 grupos devem poder desativar as respostas padroes pra
deixar quais eles querem ... separado por equipe se um desativar e outro nao nao
afeta o outro".

* `atendimento_modelos_desativados`: uma linha por resposta + equipe (101 Londres,
  102 Israel, 103 Marrocos). Sem linha, a resposta está ativa para a equipe. Nasce
  vazia: nada muda para ninguém até alguém desativar. Apagar a resposta leva as
  linhas dela (CASCADE).
* `atendimento_modelos.ativo` não muda: é o do admin e vale para todos.

DOWNGRADE: apaga a tabela. As respostas que as equipes tinham desativado voltam a
aparecer para elas.

Revision ID: 0454_atendimento_modelo_desativado
Revises: 0453_estoque_transferencias
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0454_atendimento_modelo_desativado"
down_revision: str | None = "0453_estoque_transferencias"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "atendimento_modelos_desativados",
        sa.Column(
            "modelo_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.atendimento_modelos.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("equipe", sa.Integer(), primary_key=True),
        sa.Column(
            "desativada_por",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "desativada_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("atendimento_modelos_desativados", schema=SCHEMA)
