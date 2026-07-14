# ruff: noqa: E501
"""rename chamados -> logistica (+ rastreio/chamado cols) + logistica_status

Renomeia a tabela `chamados` (recém-criada, vazia) pra `logistica`, adiciona as
colunas `rastreio` e `chamado` (aba Logística) e cria a tabela
`logistica_status` (aba Status: cadastro do que fazer pra cada STATUS
PLATAFORMA).

Revision ID: 0182_logistica
Revises: 0181_chamados
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0182_logistica"
down_revision: str | None = "0181_chamados"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    # 1) Renomeia a tabela + os índices.
    op.rename_table("chamados", "logistica", schema=SCHEMA)
    op.execute(f'ALTER INDEX "{SCHEMA}".ix_chamados_data RENAME TO ix_logistica_data')
    op.execute(f'ALTER INDEX "{SCHEMA}".ix_chamados_pedido_bling RENAME TO ix_logistica_pedido_bling')

    # 2) Novas colunas da aba Logística.
    op.add_column("logistica", sa.Column("rastreio", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column("logistica", sa.Column("chamado", sa.Text(), nullable=True), schema=SCHEMA)

    # 3) Aba Status.
    op.create_table(
        "logistica_status",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("status_plataforma", sa.Text(), nullable=False),
        sa.Column("alterar_status_bling", sa.Text(), nullable=True),
        sa.Column("abrir_chamado", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("mensagem_chamado", sa.Text(), nullable=True),
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


def downgrade() -> None:
    op.drop_table("logistica_status", schema=SCHEMA)
    op.drop_column("logistica", "chamado", schema=SCHEMA)
    op.drop_column("logistica", "rastreio", schema=SCHEMA)
    op.execute(f'ALTER INDEX "{SCHEMA}".ix_logistica_pedido_bling RENAME TO ix_chamados_pedido_bling')
    op.execute(f'ALTER INDEX "{SCHEMA}".ix_logistica_data RENAME TO ix_chamados_data')
    op.rename_table("logistica", "chamados", schema=SCHEMA)
