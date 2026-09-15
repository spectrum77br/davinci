"""logistica_robo_comando + suspensão de entrega na linha

Vinicius, 15/09/2026: botão "Suspender entrega" no Envio próprio da Amazon.
O Melhor Envio não tem API pra isso — só o painel (Meus envios › Envios
postados › Ações do envio › Suspender entrega). Quem clica é o executor
(apps/executor, o braço local que já opera a Shopee via AdsPower); o DaVinci
só enfileira o comando e mostra o resultado.

- `logistica_robo_comando`: fila de comandos do robô da Logística (hoje só
  `melhorenvio_suspender`), no mesmo modelo lease/resultado da Marketing.
- `logistica.suspensao_*`: estado visível na linha (pendente | solicitada |
  falhou), quando foi pedido e o detalhe do robô.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0275_logistica_robo_comando"
down_revision: str | None = "0274_logistica_status_lido_em"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "logistica", sa.Column("suspensao_status", sa.Text(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "logistica",
        sa.Column("suspensao_em", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "logistica", sa.Column("suspensao_detalhe", sa.Text(), nullable=True), schema=SCHEMA
    )
    op.create_table(
        "logistica_robo_comando",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "logistica_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.logistica.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("acao", sa.Text(), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_logistica_robo_comando_status",
        "logistica_robo_comando",
        ["status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_logistica_robo_comando_logistica_id",
        "logistica_robo_comando",
        ["logistica_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_logistica_robo_comando_logistica_id", table_name="logistica_robo_comando", schema=SCHEMA
    )
    op.drop_index(
        "ix_logistica_robo_comando_status", table_name="logistica_robo_comando", schema=SCHEMA
    )
    op.drop_table("logistica_robo_comando", schema=SCHEMA)
    op.drop_column("logistica", "suspensao_detalhe", schema=SCHEMA)
    op.drop_column("logistica", "suspensao_em", schema=SCHEMA)
    op.drop_column("logistica", "suspensao_status", schema=SCHEMA)
