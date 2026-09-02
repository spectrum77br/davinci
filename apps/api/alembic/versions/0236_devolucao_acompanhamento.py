# ruff: noqa: E501, S608
"""Acompanhamento de devoluções (folha do Eduardo, 2026-09-02).

Duas peças pra aba "Acompanhamento" da página de Devoluções:

1. `bling_orders.aguardando_devolucao_data` (DATE) — dia em que o pedido ENTROU
   na situação 83957 (Aguardando Devolução). Carimbada pelo ingest (mesmo
   padrão da em_andamento_data); aqui faz o backfill dos pedidos que JÁ estão
   em 83957: usa a última mudança de situação registrada na margem_audit
   (valor_novo='83957') e, sem trilha, cai no updated_at (aproximação).

2. Tabela `devolucao_rastreio` — rastreio manual por PEDIDO (numero do Bling):
   código de rastreio, última localização e a data em que a localização mudou
   pela última vez ("data da última movimentação").

Revision ID: 0236_devolucao_acompanhamento
Revises: 0235_vw_bling_pedidos_z_sku_queima_minima
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

revision: str = "0236_devolucao_acompanhamento"
down_revision: str | None = "0235_vw_bling_pedidos_z_sku_queima_minima"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')

    op.add_column(
        "bling_orders",
        sa.Column("aguardando_devolucao_data", sa.Date(), nullable=True),
        schema=SCHEMA,
    )

    op.create_table(
        "devolucao_rastreio",
        sa.Column("pedido_bling", sa.Text(), primary_key=True),
        sa.Column("rastreio", sa.Text(), nullable=True),
        sa.Column("localizacao", sa.Text(), nullable=True),
        sa.Column("localizacao_data", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_by",
            PG_UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema=SCHEMA,
    )

    # Backfill: pedidos hoje em Aguardando Devolução ganham a data de entrada.
    # Fonte 1: margem_audit (mudanças feitas PELO app); fonte 2: updated_at
    # (aproximação — mudanças feitas direto no Bling não têm trilha).
    op.execute(
        f"""
        UPDATE "{SCHEMA}".bling_orders bo
        SET aguardando_devolucao_data = COALESCE(
            (
                SELECT (max(ma.created_at) AT TIME ZONE 'America/Sao_Paulo')::date
                FROM "{SCHEMA}".margem_audit ma
                WHERE ma.acao = 'situacao'
                  AND ma.valor_novo = '83957'
                  AND ma.pedido_bling = bo.numero
            ),
            (bo.updated_at AT TIME ZONE 'America/Sao_Paulo')::date
        )
        WHERE bo.situacao = '83957'
          AND bo.aguardando_devolucao_data IS NULL
        """
    )


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.drop_table("devolucao_rastreio", schema=SCHEMA)
    op.drop_column("bling_orders", "aguardando_devolucao_data", schema=SCHEMA)
