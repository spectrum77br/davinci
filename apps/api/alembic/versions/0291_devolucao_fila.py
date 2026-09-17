"""devolucao_rastreio.fila_manual — abas Acompanhamento × Fraude da tela Devoluções

Vinicius, 17/09/2026: "na aba A devolver uma pessoa cuida só dos pedidos que
foram devolvidos e que vão devolver; na aba Fraude é outra pessoa, ela cuida
dos pedidos que o cliente alega que chegou vazio, que pede reembolso sem
devolução". A regra automática vem do tipo do caso no marketplace
(`devolucao_tipo_auto` = REFUND → Fraude); esta coluna guarda a escolha feita
na mão pelo botão "mover", que passa a valer mais que a regra.

  • `fila_manual`: 'acompanhamento' | 'fraude' | NULL (= automático).
  • `fila_manual_at`: quando foi movido (UTC).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0291_devolucao_fila"
down_revision: str | None = "0290_dm_contexto_marca"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("devolucao_rastreio", sa.Column("fila_manual", sa.Text(), nullable=True))
    op.add_column(
        "devolucao_rastreio",
        sa.Column("fila_manual_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("devolucao_rastreio", "fila_manual_at")
    op.drop_column("devolucao_rastreio", "fila_manual")
