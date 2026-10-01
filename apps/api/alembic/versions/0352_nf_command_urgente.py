"""nf_command_urgente — pedido enfileirado na mão passa na frente da fila

Vinicius, 01/10/2026: enfileirou o 300385 pelo painel, a NF saiu em 4 min,
mas a etiqueta ficou esperando o passe horário do robô ("toda vez que eu
clicar ele não esperar isso de hora em hora"). O comando ganha a marca
`urgente`: o lease entrega esses primeiro e a etiqueta urgente sai no loop
contínuo, que hoje pede tudo menos `imprimir_etiqueta`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0352_nf_command_urgente"
down_revision: str | None = "0351_denuncia_robo_tratadas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "nf_command",
        sa.Column(
            "urgente", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("nf_command", "urgente", schema=SCHEMA)
