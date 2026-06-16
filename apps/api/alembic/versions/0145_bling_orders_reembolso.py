# ruff: noqa: E501, S608
"""bling_orders.reembolso: coluna float8 + backfill a partir dos refunds

Cria `davinci.bling_orders.reembolso` (double precision, default 0) e faz o
backfill com a soma (com sinal) dos reembolsos lançados na página de Reembolso
(`davinci.refunds`) para cada pedido.

Matching: `refunds.pedido_bling` é o número do pedido (texto). Resolvemos
número -> `bling_id` (1:1 em produção) e gravamos o MESMO total em todas as
linhas (itens) daquele `bling_id`. O valor é a soma de todos os refunds do
pedido (vários tipos/linhas somam), já com o sinal correto (Cliente é forçado
<= 0 no schema). A vw_bling_pedidos depois rateia esse total por item via
`item_proportion`, então somar o valor cheio em cada linha NÃO duplica.

A agregação dos refunds é feita por número ANTES do join com bling_orders, para
não multiplicar o reembolso pelo nº de itens do pedido (fan-out).

Revision ID: 0145_bling_orders_reembolso
Revises: 0144_refund_reembolso_custo_manutencao_negativo
Create Date: 2026-06-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0145_bling_orders_reembolso"
down_revision: str | None = "0144_refund_reembolso_custo_manutencao_negativo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

_BACKFILL = f"""
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
            GROUP BY r.pedido_bling
        ) agg
        JOIN "{SCHEMA}"."bling_orders" b
          ON b.numero = agg.numero
         AND b.bling_id IS NOT NULL
    ) sub
    WHERE bo.bling_id = sub.bling_id
"""


def upgrade() -> None:
    bind = op.get_bind()
    has_col = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = :s AND table_name = 'bling_orders' "
            "AND column_name = 'reembolso'"
        ),
        {"s": SCHEMA},
    ).first()
    if not has_col:
        op.add_column(
            "bling_orders",
            sa.Column(
                "reembolso",
                sa.Float(),
                nullable=True,
                server_default=sa.text("0"),
            ),
            schema=SCHEMA,
        )
    op.execute(_BACKFILL)


def downgrade() -> None:
    op.drop_column("bling_orders", "reembolso", schema=SCHEMA)
