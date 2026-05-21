"""StockCheck — per-user "conferido" checkbox + free-text obs.

Backs the checkbox column on every tab of /controle-estoque. Unique on
(user_id, section, reference_id, reference_date) so the toggle endpoint
can upsert without explicit existence checks. `section` is one of
"estoque" / "pedido" / "envio" so a single user can mark the same SKU
checked once for the Estoque tab AND once for the Pedidos tab without
collision.
"""
from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Date, ForeignKey, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class StockCheck(Base, TimestampMixin):
    __tablename__ = "stock_checks"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "section", "reference_id", "reference_date",
            name="uq_stock_checks_user_section_ref_date",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    section: Mapped[str] = mapped_column(Text, nullable=False)
    reference_id: Mapped[str] = mapped_column(Text, nullable=False)
    reference_date: Mapped[date] = mapped_column(Date, nullable=False)
    conferido: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
