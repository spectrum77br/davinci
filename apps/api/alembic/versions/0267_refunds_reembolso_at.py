"""refunds.reembolso_at — quando o valor do Reembolso foi lançado no DaVinci

Eduardo, 14/09/2026: a tela de Reembolso só mostrava a data do pedido; ele
quer ver quando a pessoa (agência) lançou o valor na coluna Reembolso.
Carimbado pelo POST/PATCH do refund (e pelo sync de manutenção da devolução)
sempre que `reembolso` muda para um valor; limpo quando volta a vazio.

Backfill aproximado: quem já tem valor (≠ 0) recebe o `updated_at` da linha
— é a última alteração, não necessariamente a do lançamento, mas é o melhor
registro que existe (não há histórico de edições de refund).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0267_refunds_reembolso_at"
down_revision: str | None = "0266_certificacoes_inmetro"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "refunds",
        sa.Column("reembolso_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.execute(
        f"UPDATE {SCHEMA}.refunds SET reembolso_at = updated_at "
        "WHERE reembolso IS NOT NULL AND reembolso <> 0 AND reembolso_at IS NULL"
    )


def downgrade() -> None:
    op.drop_column("refunds", "reembolso_at", schema=SCHEMA)
