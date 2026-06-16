"""correção de sinal: custo_manutencao entra como débito (negativo) no reembolso

Revision ID: 0144_refund_reembolso_custo_manutencao_negativo
Revises: 0143_refund_reembolso_custo_manutencao

A 0143 somou o custo_manutencao como POSITIVO no reembolso dos refunds de
Manutenção. A regra correta é débito: o custo entra NEGATIVO (custo 30 ->
reembolso -= 30). Como a 0143 já rodou em prod, esta migration corrige o sinal
dos registros: hoje o valor é `original + custo`, queremos `original - custo`,
logo subtraímos `2 * custo` das mesmas linhas (mesmo matching da 0143).

Determinístico: usa o mesmo casamento (tipo='Manutenção', pedido_bling+conta) e
o mesmo SUM(custo_manutencao). O downgrade restaura o estado da 0143 (+custo).
"""

from alembic import op

revision = "0144_refund_reembolso_custo_manutencao_negativo"
down_revision = "0143_refund_reembolso_custo_manutencao"
branch_labels = None
depends_on = None

SCHEMA = "davinci"

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


def _shift(*, factor: str) -> None:
    # factor='-2' no upgrade (de +custo para -custo); '+2' no downgrade.
    op.execute(
        f"""
        UPDATE "{SCHEMA}"."refunds" r
        SET reembolso = COALESCE(r.reembolso, 0) + ({factor}) * sub.total_custo,
            updated_at = now()
        FROM ({_SUB}) sub
        WHERE r.tipo = 'Manutenção'
          AND r.pedido_bling = sub.pedido_bling
          AND btrim(r.conta) = sub.conta
        """
    )


def upgrade() -> None:
    _shift(factor="-2")


def downgrade() -> None:
    _shift(factor="+2")
