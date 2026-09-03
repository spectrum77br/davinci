# ruff: noqa: E501
"""Saldo Efetivo manual na aba Margem (Eduardo, 2026-09-03).

"coloque a opção de preencher manualmente tbm, esta automatico mas e bom dar
pra preencher na mao tbm" — em ML/Shopee/TikTok o Saldo Efetivo fica EM BRANCO
até o líquido REAL da plataforma sincronizar (regra de 01/09, sem projeção e
sem Bling). Esta tabela guarda o valor digitado NA MÃO pelo operador para
preencher esse vazio; quando o repasse real chega, ele VENCE o manual
(COALESCE(real, manual) no router de margens).

Grão de ITEM (verificar_margem.bling_order_item_id), sem FK: o snapshot é
rebuilt a cada 30min e o id vem do espelho bling_orders. pedido_bling/sku são
denormalizados só para debug/auditoria.

Revision ID: 0237_margem_saldo_manual
Revises: 0236_devolucao_acompanhamento
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

revision: str = "0237_margem_saldo_manual"
down_revision: str | None = "0236_devolucao_acompanhamento"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')

    op.create_table(
        "margem_saldo_manual",
        sa.Column("bling_order_item_id", PG_UUID(as_uuid=True), primary_key=True),
        sa.Column("pedido_bling", sa.Text(), nullable=True),
        sa.Column("sku", sa.Text(), nullable=True),
        sa.Column("valor", sa.Numeric(14, 2), nullable=False),
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


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.drop_table("margem_saldo_manual", schema=SCHEMA)
