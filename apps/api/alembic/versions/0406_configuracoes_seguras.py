"""Tabela configuracoes_seguras — tokens de serviços de fora guardados cifrados (15/10/2026).

Primeiro uso: token do Melhor Envio colado pela operação em Conciliação › Amazon
(o do `.env` do servidor está vencido e ninguém da operação consegue trocá-lo).

Revision ID: 0406_configuracoes_seguras
Revises: 0405_conciliacao_amazon_kia_poofy
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0406_configuracoes_seguras"
down_revision: str | None = "0405_conciliacao_amazon_kia_poofy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "configuracoes_seguras",
        sa.Column("chave", sa.String(60), primary_key=True),
        sa.Column("valor", sa.LargeBinary(), nullable=False),
        sa.Column(
            "detalhe",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("atualizado_por", sa.String(255), nullable=True),
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
        schema="davinci",
    )


def downgrade() -> None:
    op.drop_table("configuracoes_seguras", schema="davinci")
