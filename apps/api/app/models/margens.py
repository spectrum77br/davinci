from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Float, Integer, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Margens(Base):
    __tablename__ = "margens"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )
    data: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pedido_bling: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pedido_plataforma: Mapped[str | None] = mapped_column(Text, nullable=True)
    conta: Mapped[str | None] = mapped_column(Text, nullable=True)
    sku: Mapped[str | None] = mapped_column(Text, nullable=True)
    produtos: Mapped[str | None] = mapped_column(Text, nullable=True)
    custo: Mapped[float | None] = mapped_column(Float, nullable=True)
    margem: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str | None] = mapped_column(Text, nullable=True)
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
