"""chamado_pedidos — pedidos cobertos por um chamado em lote (atraso na postagem)

Eduardo, 15/09/2026 (Controle de Estoque › Pedidos): "um botão onde eu vou
selecionar todos os pedidos e o sistema abre um chamado em cada loja — não
precisa ser um chamado para cada pedido, pode juntar: todos ML Aguiar num
único chamado". O texto justifica o atraso da postagem: poucos minutos depois
do corte = fila na agência; horas depois, no mesmo dia = falta de energia.

O chamado (tabela `chamados`) continua com um pedido só em `pedido_bling` (o
1º do lote); esta tabela liga TODOS os pedidos ao chamado, com o motivo, o
corte e a hora da postagem de cada um — é o que a aba Pedidos usa pra
mostrar o chamado em cada linha e não abrir de novo pro mesmo pedido.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0276_chamado_pedidos"
down_revision: str | None = "0275_logistica_robo_comando"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "chamado_pedidos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "chamado_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.chamados.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pedido_bling", sa.Text(), nullable=False),
        sa.Column("pedido_marketplace", sa.Text(), nullable=True),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column("corte_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("postagem_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("chamado_id", "pedido_bling", name="uq_chamado_pedidos_chamado_pedido"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_chamado_pedidos_chamado_id", "chamado_pedidos", ["chamado_id"], schema=SCHEMA
    )
    op.create_index(
        "ix_chamado_pedidos_pedido_bling", "chamado_pedidos", ["pedido_bling"], schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_index("ix_chamado_pedidos_pedido_bling", table_name="chamado_pedidos", schema=SCHEMA)
    op.drop_index("ix_chamado_pedidos_chamado_id", table_name="chamado_pedidos", schema=SCHEMA)
    op.drop_table("chamado_pedidos", schema=SCHEMA)
