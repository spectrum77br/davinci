from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Segment(Base, TimestampMixin):
    __tablename__ = "segments"
    __table_args__ = (
        UniqueConstraint("parent_id", "slug", name="uq_segments_parent_slug"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    parent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("segments.id", ondelete="CASCADE"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    min_margin: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    # Physical defaults per subsegment: dimensions in cm, peso in kg. Nullable.
    altura: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    largura: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    comprimento: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    peso: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)

    # lazy="selectin": carrega junto em qualquer SELECT de Segment (inclusive
    # session.refresh), então SegmentOut.special_dates nunca dispara lazy-load
    # em contexto async.
    special_dates: Mapped[list["SegmentSpecialDate"]] = relationship(
        "SegmentSpecialDate",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="SegmentSpecialDate.date_start",
    )


class SegmentSpecialDate(Base, TimestampMixin):
    """Datas Especiais — janela de exceção da triagem de margem por segmento.

    Pedido do Eduardo (01/09/2026): "em segmentos, que as margens que
    utilizamos em margens, vamos colocar um novo campo chamado datas
    especiais, que é a regra que vamos aprovar, para exceção, por exemplo
    está com margem negativa, aprova". Dentro do período (date_start..
    date_end, datas no fuso de São Paulo, inclusivo dos dois lados), pedido
    cujo segmento é este — ou um DESCENDENTE dele (regra na raiz vale para a
    família toda) — não é travado por margem baixa:
      • min_margin NULL  → aprova qualquer margem (até negativa);
      • min_margin cheio → piso especial, em FRAÇÃO como segments.min_margin
        (-0.15 = -15%); aprova enquanto a margem ficar >= esse piso.
    Consumido por _MARGEM_DATA_ESPECIAL_SQL (routers/margens.py), que o
    auto-hold herda por import — o robô também para de segurar no período.
    """

    __tablename__ = "segment_special_dates"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    segment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("segments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    date_start: Mapped[date] = mapped_column(Date, nullable=False)
    date_end: Mapped[date] = mapped_column(Date, nullable=False)
    min_margin: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
