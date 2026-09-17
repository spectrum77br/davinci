"""chamados.status_plataforma / status_plataforma_at — coluna "Status" da aba
Chamados (o que a plataforma diz do chamado, e desde quando)

Vinicius, 17/09/2026: "outra coisa que precisamos também é o status. tipo o
chamado está com status finalizado, aberto de todas as plataformas". Até aqui
a resposta da plataforma só entrava no histórico como texto e o chamado
fechava sozinho no fim — no meio do caminho a aba não dizia se estava em
análise, se a plataforma pediu prova ou se o reembolso já tinha sido pago.

Colunas:
  • `status_plataforma`: código do status OFICIAL lido pela API (Shopee
    get_return_detail + escrow, TikTok returns/search, ML claim) —
    `em_analise`, `prova`, `reembolso_pago`, `ganhamos`, `perdemos`,
    `encerrado`. NULL = a API não disse nada; a aba deriva do histórico
    (aguardando plataforma / plataforma respondeu / precisa de humano).
  • `status_plataforma_at`: desde quando (UTC) — hora da plataforma quando
    ela informa, senão a hora em que o sync viu.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0287_chamado_status_plataforma"
down_revision: str | None = "0286_devolucao_prazo_acao"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chamados", sa.Column("status_plataforma", sa.Text(), nullable=True))
    op.add_column(
        "chamados",
        sa.Column("status_plataforma_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chamados", "status_plataforma_at")
    op.drop_column("chamados", "status_plataforma")
