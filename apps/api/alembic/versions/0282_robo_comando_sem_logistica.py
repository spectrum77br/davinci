"""fila do robô aceita comando que não pertence a uma linha da Logística

A tabela nasceu para o "Suspender entrega" do Melhor Envio, então
`logistica_id` era obrigatório. O robô do Mac passou a receber tarefas que não
são de um pedido — a primeira é a leitura diária da caixa do Tuta atrás dos
códigos de devolução. Coluna vira opcional; nada mais muda, e todo comando
existente continua com o seu vínculo.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0282_robo_comando_sem_logistica"
down_revision: str | None = "0281_marketing_postagens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.alter_column(
        "logistica_robo_comando",
        "logistica_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=True,
        schema=SCHEMA,
    )


def downgrade() -> None:
    # Só volta se não houver comando sem vínculo.
    op.execute(sa.text("DELETE FROM davinci.logistica_robo_comando WHERE logistica_id IS NULL"))
    op.alter_column(
        "logistica_robo_comando",
        "logistica_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=False,
        schema=SCHEMA,
    )
