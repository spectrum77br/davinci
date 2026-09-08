# ruff: noqa: E501
"""Conector do Claude — Eduardo, 2026-09-08.

"eu queria fazer uma coisa: mandar mensagem de áudio pelo celular, na caixa de
mensagens do Claude, e ele já gravar no banco... é só para ele mandar o áudio e
a gente gravar na aba Tarefas". O chefe usa o próprio chat do Claude; o Claude
fala com o DaVinci por um conector (MCP) cujo segredo vai no endereço. Um
token por pessoa, revogável na aba Tarefas.

Revision ID: 0251_claude_conector
Revises: 0250_logistica_chamado_auto
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0251_claude_conector"
down_revision: str | None = "0250_logistica_chamado_auto"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.create_table(
        "claude_conectores",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("nome", sa.String(120), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultimo_erro", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_claude_conectores_user_id", "claude_conectores", ["user_id"])
    op.create_index("ix_claude_conectores_token_hash", "claude_conectores", ["token_hash"], unique=True)


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.drop_index("ix_claude_conectores_token_hash", table_name="claude_conectores")
    op.drop_index("ix_claude_conectores_user_id", table_name="claude_conectores")
    op.drop_table("claude_conectores")
