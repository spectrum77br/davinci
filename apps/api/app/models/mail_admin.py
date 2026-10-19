"""Controles humanos laterais: acesso à leitura, lixeira local e auditoria.

Nunca alteram o estado do Tuta, o conteúdo cifrado ou a decisão da ponte.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class MailAcesso(Base, TimestampMixin):
    __tablename__ = "mail_acessos"
    __table_args__ = (
        CheckConstraint("modo IN ('padrao', 'selecionadas', 'bloqueado')", name="modo"),
        *(
            CheckConstraint(f"jsonb_typeof({col}) = 'array'", name=f"{col}_lista")
            for col in (
                "lojas_permitidas",
                "lojas_bloqueadas",
                "tipos_permitidos",
                "pedidos_permitidos",
            )
        ),
        Index("ix_mail_acessos_user_id", "user_id"),
    )
    mailbox_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("mail_mailboxes.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    modo: Mapped[str] = mapped_column(String(16), nullable=False)
    # Guardam também as chaves equivalentes ficha/integração. A API devolve a canônica.
    lojas_permitidas: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    lojas_bloqueadas: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    tipos_permitidos: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    pedidos_permitidos: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class MailLixeira(Base, TimestampMixin):
    __tablename__ = "mail_lixeira"
    __table_args__ = (Index("ix_mail_lixeira_excluido_em", "excluido_em", "message_id"),)
    message_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("mail_messages.id", ondelete="CASCADE"), primary_key=True
    )
    excluido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    excluido_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    motivo: Mapped[str | None] = mapped_column(String(300))
    restaurado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    restaurado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class MailAdminEvento(Base):
    """Somente identificadores e a configuração: nunca assunto, corpo ou endereço."""

    __tablename__ = "mail_admin_eventos"
    __table_args__ = (Index("ix_mail_admin_eventos_criado_em", "criado_em"),)
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    mailbox_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("mail_mailboxes.id", ondelete="CASCADE")
    )
    autor_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    acao: Mapped[str] = mapped_column(String(32))
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    dados: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
