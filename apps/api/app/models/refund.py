from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Refund(Base, TimestampMixin):
    __tablename__ = "refunds"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    data: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pedido_bling: Mapped[str | None] = mapped_column(Text, nullable=True)
    pedido_marketplace: Mapped[str | None] = mapped_column(Text, nullable=True)
    plataforma: Mapped[str | None] = mapped_column(Text, nullable=True)
    conta: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    tipo: Mapped[str | None] = mapped_column(Text, nullable=True)
    prejuizo: Mapped[float | None] = mapped_column(Float, nullable=True)
    reembolso: Mapped[float | None] = mapped_column(Float, nullable=True)
    chamado: Mapped[str | None] = mapped_column(Text, nullable=True)
    operacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    conferido: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Rastreio interno de autoria (DB-only): preenchido no POST /api/refunds a
    # partir do usuário autenticado. NÃO é exposto em RefundOut nem na UI.
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
