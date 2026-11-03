"""respostas prontas do /atendimento por equipe: `atendimento_modelos.equipe`

Eduardo, 03/11/2026: "separe por 3 equipes que separamos la mas as q ja tem la
deixe pra todos mas as novas que eles vao criar vai ter essa ideia de
especifico por equipe".

* `atendimento_modelos.equipe` (int, NULL): o número da equipe de vendas de
  `users.sales_teams` / `store_info.sales_team` (101 Londres, 102 Israel,
  103 Marrocos, a 0441). NULL = para todos. As respostas que já existem ficam
  NULL — continuam valendo para todo mundo, sem UPDATE nenhum.
* Índice na coluna: a lista de cada pessoa é "NULL ou a equipe dela".

DOWNGRADE: tira o índice e a coluna. As respostas de equipe passam a valer
para todos (o código de antes não conhece equipe).

Revision ID: 0443_atendimento_modelo_equipe
Revises: 0442_pricing_conferencia
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0443_atendimento_modelo_equipe"
down_revision: str | None = "0442_pricing_conferencia"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
INDICE = "ix_atendimento_modelos_equipe"


def upgrade() -> None:
    op.add_column(
        "atendimento_modelos", sa.Column("equipe", sa.Integer(), nullable=True), schema=SCHEMA
    )
    op.create_index(INDICE, "atendimento_modelos", ["equipe"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index(INDICE, table_name="atendimento_modelos", schema=SCHEMA)
    op.drop_column("atendimento_modelos", "equipe", schema=SCHEMA)
