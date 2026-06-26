# ruff: noqa: E501, S608
"""refunds.conferido_at: timestamp de quando o refund foi marcado conferido

Antes não havia registro de QUANDO `conferido` virou true — só `updated_at`,
que se move a cada edição. A página de Valuation (quadro "Operacional — 3 meses",
linha Reembolso) precisa agrupar os reembolsos conferidos por mês a partir dessa
data, então criamos a coluna dedicada.

`patch_refund` carimba `conferido_at` na transição false→true (e limpa em
true→false). Backfill dos já conferidos com `updated_at` (melhor esforço — é a
melhor aproximação disponível para o histórico; refunds antigos podem ter o
updated_at deslocado por edições posteriores).

Revision ID: 0160_refunds_conferido_at
Revises: 0159_vw_bling_item_mp_weight_cost_priority
Create Date: 2026-06-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0160_refunds_conferido_at"
down_revision: str | None = "0159_vw_bling_item_mp_weight_cost_priority"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

_BACKFILL = f"""
    UPDATE "{SCHEMA}"."refunds"
    SET conferido_at = updated_at
    WHERE conferido = true
      AND conferido_at IS NULL
"""


def upgrade() -> None:
    bind = op.get_bind()
    has_col = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = :s AND table_name = 'refunds' "
            "AND column_name = 'conferido_at'"
        ),
        {"s": SCHEMA},
    ).first()
    if not has_col:
        op.add_column(
            "refunds",
            sa.Column("conferido_at", sa.DateTime(timezone=True), nullable=True),
            schema=SCHEMA,
        )
    op.execute(_BACKFILL)


def downgrade() -> None:
    op.drop_column("refunds", "conferido_at", schema=SCHEMA)
