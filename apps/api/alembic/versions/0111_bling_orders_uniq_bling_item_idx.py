"""bling_orders: UNIQUE (bling_id, item_index) para upsert estável

Revision ID: 0111_bling_orders_uniq_bling_item_idx
Revises: 0110_devolution_estoque_destino
Create Date: 2026-05-30

`upsert_order` passa a fazer INSERT ... ON CONFLICT (bling_id, item_index)
em vez de DELETE+INSERT. Assim `bling_orders.id` (uuid4) para de trocar a
cada re-ingest — o que defasava o PK guardado no snapshot
`verificar_margem` e gerava `bling_order_not_found` na página de margens.
A UNIQUE também serializa webhooks concorrentes do mesmo pedido (antes
geravam linhas duplicadas; havia 528 grupos (bling_id, item_index)
duplicados no momento desta migration).

Antes de criar o índice, deduplica mantendo 1 linha por (bling_id,
item_index) — preferindo a mais "rica" (com valorbase / preco_custo /
status preenchidos), tie-break por ctid — e removendo as demais.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0111_bling_orders_uniq_bling_item_idx"
down_revision = "0110_devolution_estoque_destino"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    # 1) Dedup: 1 linha por (bling_id, item_index). Preferimos a linha com
    #    dados financeiros/status preenchidos; desempate determinístico por ctid.
    op.execute(
        f"""
        DELETE FROM {SCHEMA}.bling_orders bo
        USING (
            SELECT ctid,
                   row_number() OVER (
                       PARTITION BY bling_id, item_index
                       ORDER BY (valorbase IS NOT NULL) DESC,
                                (preco_custo IS NOT NULL) DESC,
                                (status IS NOT NULL) DESC,
                                ctid DESC
                   ) AS rn
            FROM {SCHEMA}.bling_orders
            WHERE bling_id IS NOT NULL
        ) d
        WHERE bo.ctid = d.ctid
          AND d.rn > 1
        """  # noqa: S608
    )

    # 2) UNIQUE index para suportar ON CONFLICT (bling_id, item_index).
    op.create_index(
        "uq_bling_orders_bling_id_item_index",
        "bling_orders",
        ["bling_id", "item_index"],
        unique=True,
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_bling_orders_bling_id_item_index",
        table_name="bling_orders",
        schema=SCHEMA,
    )
