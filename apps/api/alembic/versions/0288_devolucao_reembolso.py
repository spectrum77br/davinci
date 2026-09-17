"""devolucao_rastreio.reembolso_*_auto — coluna "Reembolso" da aba
Acompanhamento de Devoluções (Sim/Não + valor e data)

Vinicius, 17/09/2026: "o importante é saber se estamos com o dinheiro ainda
ou se já devolveu para o cliente". Preenchido pelo sync do retorno
(services/devolucao_rastreio_sync) a partir do caso no marketplace (TikTok
caso concluído, Shopee caso ACCEPTED, ML pagamento estornado) cruzado com o
extrato financeiro já baixado (marketplace_order_financials: desconto do
reembolso no escrow/statement e compensação paga pela Shopee).

Colunas:
  • `reembolso_auto`: True = saiu dinheiro do nosso; False = não saiu (caso
    vivo/cancelado, ou a plataforma pagou do próprio bolso — ML cobertura/BPP,
    Shopee compensação); NULL = não se sabe.
  • `reembolso_valor_auto` / `reembolso_em_auto`: quanto foi devolvido ao
    cliente e quando (UTC) — mesmo quando a plataforma cobriu.
  • `reembolso_detalhe_auto`: texto curto do balão (fonte/motivo).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0288_devolucao_reembolso"
down_revision: str | None = "0287_chamado_status_plataforma"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("devolucao_rastreio", sa.Column("reembolso_auto", sa.Boolean(), nullable=True))
    op.add_column(
        "devolucao_rastreio",
        sa.Column("reembolso_valor_auto", sa.Numeric(14, 2), nullable=True),
    )
    op.add_column(
        "devolucao_rastreio",
        sa.Column("reembolso_em_auto", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "devolucao_rastreio", sa.Column("reembolso_detalhe_auto", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("devolucao_rastreio", "reembolso_detalhe_auto")
    op.drop_column("devolucao_rastreio", "reembolso_em_auto")
    op.drop_column("devolucao_rastreio", "reembolso_valor_auto")
    op.drop_column("devolucao_rastreio", "reembolso_auto")
