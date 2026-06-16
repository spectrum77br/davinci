"""backfill: soma custo_manutencao das devoluções no reembolso dos refunds de Manutenção

Revision ID: 0143_refund_reembolso_custo_manutencao
Revises: 0142_vw_conciliacao_margens_perdimento_margem

A partir de agora o custo de manutenção informado na devolução é somado ao
`reembolso` do refund de Manutenção correspondente (mesmo pedido + conta),
respeitando o sinal. Este backfill aplica a mesma regra retroativamente aos
registros que já existem: para cada refund de Manutenção, soma no reembolso o
total de custo_manutencao das devoluções casadas por (pedido_bling, conta).

Idempotente por execução única (roda uma vez no upgrade). O downgrade desfaz
exatamente a mesma soma.
"""

from alembic import op

revision = "0143_refund_reembolso_custo_manutencao"
down_revision = "0142_vw_conciliacao_margens_perdimento_margem"
branch_labels = None
depends_on = None

SCHEMA = "davinci"

# Casa devoluções com custo de manutenção aos refunds de Manutenção pelo par
# (pedido_bling, conta). Soma todos os custos do mesmo pedido/conta antes de
# aplicar, evitando múltiplos UPDATEs sobre a mesma linha de refund.
_SUB = f"""
    SELECT
        pedido_bling,
        btrim(conta) AS conta,
        SUM(COALESCE(custo_manutencao, 0)) AS total_custo
    FROM "{SCHEMA}"."devolutions"
    WHERE custo_manutencao IS NOT NULL
      AND custo_manutencao <> 0
      AND pedido_bling IS NOT NULL
    GROUP BY pedido_bling, btrim(conta)
"""


def _backfill(*, sign: str) -> None:
    op.execute(
        f"""
        UPDATE "{SCHEMA}"."refunds" r
        SET reembolso = COALESCE(r.reembolso, 0) {sign} sub.total_custo,
            updated_at = now()
        FROM ({_SUB}) sub
        WHERE r.tipo = 'Manutenção'
          AND r.pedido_bling = sub.pedido_bling
          AND btrim(r.conta) = sub.conta
        """
    )


def upgrade() -> None:
    _backfill(sign="+")


def downgrade() -> None:
    _backfill(sign="-")
