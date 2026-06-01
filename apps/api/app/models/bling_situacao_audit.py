from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class BlingSituacaoAudit(Base):
    """Trilha de auditoria das mudanças de situação de pedidos no Bling
    feitas PELO app (margens, devolução, job de envio). Cada linha registra
    quando, quem (ou sistema) e a transição antiga → nova.

    Mudanças feitas direto no painel do Bling NÃO entram aqui — a API do Bling
    não expõe o autor da mudança.
    """

    __tablename__ = "bling_situacao_audit"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
        index=True,
    )
    pedido_bling: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    bling_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    sku: Mapped[str | None] = mapped_column(Text, nullable=True)
    situacao_antiga: Mapped[str | None] = mapped_column(Text, nullable=True)
    situacao_nova: Mapped[str] = mapped_column(Text, nullable=False)
    # 'margens' | 'devolucao' | 'job_envio'
    origem: Mapped[str] = mapped_column(Text, nullable=False)
    # NULL = mudança disparada pelo sistema (job automático)
    mudado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
