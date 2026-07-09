from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Date, ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Fatura(Base, TimestampMixin):
    """Assinatura/plano recorrente que o admin quer acompanhar (ex.: plano de
    12 meses do Higgsfield). Um cron avisa quando a `data_vencimento` está
    chegando; a renovação é manual — o admin edita a data pro próximo ciclo."""

    __tablename__ = "faturas"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    servico: Mapped[str] = mapped_column(Text, nullable=False)
    plano: Mapped[str | None] = mapped_column(Text, nullable=True)
    valor: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    data_vencimento: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
