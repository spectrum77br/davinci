"""Conector do Claude: link secreto que liga o chat do Claude de uma pessoa ao
DaVinci (servidor MCP em `routers/claude_conector.py`).

Eduardo, 08/09/2026: o chefe quer mandar tarefa por ÁUDIO no próprio chat do
Claude e ela cair na aba Tarefas. O Claude (app) fala com o DaVinci por um
"conector" (MCP remoto); como o app não manda cabeçalho de autenticação, o
segredo vai no próprio endereço — um token por pessoa, revogável aqui.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ClaudeConector(Base, TimestampMixin):
    __tablename__ = "claude_conectores"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    # Quem "é" no DaVinci quando o Claude dele cria tarefa (created_by e
    # responsável padrão).
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    # sha256 do token (o token em si só aparece uma vez, na criação).
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Última mensagem que o Claude mandou (pra depurar "não criou").
    ultimo_erro: Mapped[str | None] = mapped_column(Text, nullable=True)
