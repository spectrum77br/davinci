# ruff: noqa: E501, S608
"""bling_orders.reembolso: considerar apenas refunds conferidos

Regra de negócio (2026-06-16): só refunds com `conferido = true` (check marcado
pelo usuário na página de Reembolso) entram no lucro/margem. A migration 0145
fez o backfill somando TODOS os refunds; aqui recalculamos respeitando o
conferido — zera tudo e reaplica só os conferidos.

Matching idêntico ao 0145 (numero -> bling_id, soma por pedido antes do join
para não multiplicar pelo nº de itens). A vw_bling_pedidos só lê a coluna, então
não precisa mudar.

Idempotente: recompute-from-scratch. O downgrade reaplica o backfill de TODOS os
refunds (estado pré-0147, igual ao 0145).

Revision ID: 0147_bling_orders_reembolso_so_conferido
Revises: 0146_vw_bling_pedidos_reembolso
Create Date: 2026-06-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0147_bling_orders_reembolso_so_conferido"
down_revision: str | None = "0146_vw_bling_pedidos_reembolso"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def _backfill(*, only_conferido: bool) -> None:
    conferido_clause = "AND r.conferido = true" if only_conferido else ""
    # Zera primeiro para limpar valores de pedidos que perderam elegibilidade
    # (ex.: refund não conferido que tinha entrado no 0145).
    op.execute(
        f'UPDATE "{SCHEMA}"."bling_orders" SET reembolso = 0 '
        f"WHERE reembolso IS NOT NULL AND reembolso <> 0"
    )
    op.execute(
        f"""
        UPDATE "{SCHEMA}"."bling_orders" bo
        SET reembolso = sub.total
        FROM (
            SELECT DISTINCT b.bling_id, agg.total
            FROM (
                SELECT r.pedido_bling AS numero,
                       SUM(COALESCE(r.reembolso, 0))::double precision AS total
                FROM "{SCHEMA}"."refunds" r
                WHERE r.reembolso IS NOT NULL
                  AND r.reembolso <> 0
                  AND r.pedido_bling IS NOT NULL
                  {conferido_clause}
                GROUP BY r.pedido_bling
            ) agg
            JOIN "{SCHEMA}"."bling_orders" b
              ON b.numero = agg.numero
             AND b.bling_id IS NOT NULL
        ) sub
        WHERE bo.bling_id = sub.bling_id
        """
    )


def upgrade() -> None:
    _backfill(only_conferido=True)


def downgrade() -> None:
    _backfill(only_conferido=False)
