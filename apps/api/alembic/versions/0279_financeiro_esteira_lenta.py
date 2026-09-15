"""esteira lenta do financeiro: falha de API não mata mais a linha

Contexto (15/09/2026): 9 contas Shopee com 403, Amazon com LWA vencido e o
Mercado Livre recusando refresh deixaram as APIs fora por dias. Cada tentativa
falha gastava uma das 8 do backoff, então ~3.900 pedidos terminaram com
`next_retry_at = NULL` — fora da fila para sempre. Quando o usuário renovou os
tokens, a Margem continuou em branco porque nada no sistema reabria essas
linhas.

Esta coluna separa as duas filas: `espera_lenta = false` é a fila rápida (erro
recente, backoff normal) e `true` é a esteira lenta (API fora / valor ainda não
publicado pela plataforma), que tenta 2x/dia sem gastar tentativa. Filas
separadas para um backlog antigo nunca roubar a vez dos pedidos do dia.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0279_financeiro_esteira_lenta"
down_revision: str | None = "0278_email_assinaturas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "marketplace_order_financials",
        sa.Column(
            "espera_lenta",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema="davinci",
    )
    op.create_index(
        "ix_marketplace_order_financials_esteira",
        "marketplace_order_financials",
        ["espera_lenta", "next_retry_at"],
        unique=False,
        schema="davinci",
        postgresql_where=sa.text("next_retry_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_marketplace_order_financials_esteira",
        table_name="marketplace_order_financials",
        schema="davinci",
    )
    op.drop_column("marketplace_order_financials", "espera_lenta", schema="davinci")
