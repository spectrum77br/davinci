"""o brinde do pedido não pago: o que aconteceu com ele

O dono, 05/11/2026: 3 h depois de um pedido criado e não finalizado, a oferta
(celular ganha AirTag, mala ganha chaveiro); se a pessoa comprar, um botão no
Comunicador põe o brinde no pedido.

* `atendimento_brindes`: nasce VAZIA e só ganha linha quando alguém age ou quando
  o prazo de 12 h vence, uma por loja e pedido. Sem dado de cliente. Fica COM o
  gatilho do Histórico (o daily do worker instala): quem pôs o brinde é o que o
  Histórico serve para mostrar.

DOWNGRADE: apaga a tabela. Os pedidos com oferta enviada voltam a contar como "sem
decisão" e ficam fora da fila da NF até completar 12 h no espelho; depois seguem.

Revision ID: 0455_atendimento_brindes
Revises: 0454_atendimento_modelo_desativado
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0455_atendimento_brindes"
down_revision: str | None = "0454_atendimento_modelo_desativado"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "atendimento_brindes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "integration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.integrations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pedido", sa.String(64), nullable=False),
        sa.Column("tipo", sa.String(16), nullable=True),
        sa.Column("sku", sa.String(64), nullable=True),
        sa.Column("estado", sa.String(16), nullable=False),
        sa.Column("bling_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "por",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("integration_id", "pedido", name="uq_atendimento_brindes_pedido"),
        sa.CheckConstraint(
            "estado IN ('adicionado', 'dispensado', 'expirado', 'incerto')",
            name="ck_atendimento_brindes_estado",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_atendimento_brindes_estado_em",
        "atendimento_brindes",
        ["estado", "em"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_atendimento_brindes_estado_em", table_name="atendimento_brindes", schema=SCHEMA
    )
    op.drop_table("atendimento_brindes", schema=SCHEMA)
