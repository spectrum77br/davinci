# ruff: noqa: E501
"""create chamados table

Chamados de pós-venda (casos a acompanhar) no formato da Planilha2. Registro
manual; `meli_status` (JSONB) guarda a assinatura dos 8 campos de status do
Meli que alimenta a sugestão de Status Bling candidato.

Revision ID: 0181_chamados
Revises: 0180_marketing_flash_duplicate
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0181_chamados"
down_revision: str | None = "0180_marketing_flash_duplicate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "chamados",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("data", sa.Date(), nullable=True),
        sa.Column("pedido_bling", sa.Text(), nullable=True),
        sa.Column("pedido_marketplace", sa.Text(), nullable=True),
        sa.Column("plataforma", sa.Text(), nullable=True),
        sa.Column("conta", sa.Text(), nullable=True),
        sa.Column("meli_status", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("localizacao", sa.Text(), nullable=True),
        sa.Column("status_bling", sa.Text(), nullable=True),
        sa.Column("observacao", sa.Text(), nullable=True),
        # SET NULL so a deleted user doesn't break their old chamados.
        sa.Column(
            "created_by",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_chamados_data", "chamados", ["data"], schema=SCHEMA)
    op.create_index("ix_chamados_pedido_bling", "chamados", ["pedido_bling"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_chamados_pedido_bling", table_name="chamados", schema=SCHEMA)
    op.drop_index("ix_chamados_data", table_name="chamados", schema=SCHEMA)
    op.drop_table("chamados", schema=SCHEMA)
