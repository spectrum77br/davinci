"""situacao_bling.ativo — o catálogo passa a espelhar o Bling

Eduardo, 15/09/2026: o dropdown "Situação no Bling ao fechar" (Chamados) e o
"Alterar status Bling" (Logística) listavam situações que o Bling já apagou
("Enviado Geral CI"…) — a tabela veio do userdb na migration 0017 e nunca foi
atualizada. Agora um sync diário (services/bling_situacoes_sync) lê o módulo
Vendas do Bling: situação nova entra, apagada vira `ativo = false`.

Não apaga linha: `bling_orders.situacao` e as views de pedidos ainda apontam
pros ids antigos e precisam do nome. Só os dropdowns filtram por `ativo`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0270_situacao_bling_ativo"
down_revision: str | None = "0269_chamados_sem_monitoramento"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "situacao_bling",
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("situacao_bling", "ativo", schema=SCHEMA)
