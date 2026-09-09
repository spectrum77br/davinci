# ruff: noqa: E501
"""Devoluções: prazo da plataforma para contestar — Eduardo, 2026-09-09.

"em devoluções - motivo, quando for item faltando tem que abrir chamado
automático também". Já abria; o 291835 (Shopee) perdeu a contestação porque o
motivo só foi preenchido 3 dias depois de o pacote voltar, quando o
`return_seller_due_date` já tinha passado. Agora a linha guarda o prazo da
plataforma (Shopee/TikTok), a tela mostra em vermelho quando falta < 24 h, e
o Threema avisa uma vez quando o motivo ainda está vazio perto do prazo.

Revision ID: 0252_devolucao_prazo_contestacao
Revises: 0251_claude_conector
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0252_devolucao_prazo_contestacao"
down_revision: str | None = "0251_claude_conector"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.add_column(
        "devolutions", sa.Column("prazo_contestacao", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "devolutions", sa.Column("prazo_contestacao_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "devolutions", sa.Column("aviso_prazo_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.drop_column("devolutions", "aviso_prazo_at")
    op.drop_column("devolutions", "prazo_contestacao_at")
    op.drop_column("devolutions", "prazo_contestacao")
