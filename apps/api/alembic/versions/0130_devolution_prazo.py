"""devolution: coluna `prazo` (30 dias da inserção, só Manutenção)

Revision ID: 0130_devolution_prazo
Revises: 0129_devolution_manutencao_flag

Prazo = data de inserção da devolução (created_at) + 30 dias. Só faz sentido
para condição "Manutenção" — nas demais fica NULL. Preenchido automaticamente
no create/patch quando a condição é Manutenção.

Backfill: marca o prazo das Manutenção existentes a partir do created_at.
"""

from alembic import op
import sqlalchemy as sa

revision = "0130_devolution_prazo"
down_revision = "0129_devolution_manutencao_flag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "devolutions",
        sa.Column("prazo", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE devolutions
           SET prazo = created_at + INTERVAL '30 days'
         WHERE condicao_produto = 'Manutenção'
        """
    )


def downgrade() -> None:
    op.drop_column("devolutions", "prazo")
