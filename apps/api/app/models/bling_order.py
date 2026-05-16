from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class BlingOrder(Base, TimestampMixin):
    __tablename__ = "bling_orders"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    bling_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    numero: Mapped[str | None] = mapped_column(Text, nullable=True)
    numeroloja: Mapped[str | None] = mapped_column(Text, nullable=True)
    numero_documento: Mapped[str | None] = mapped_column(Text, nullable=True)
    data: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    totalprodutos: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    total: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    situacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(Text, nullable=True)
    aprovado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    loja: Mapped[str | None] = mapped_column(Text, nullable=True)
    store_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="SET NULL"),
        nullable=True,
    )
    itens: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    valorbase: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    custofrete: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    taxacomissao: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    preco_custo: Mapped[float | None] = mapped_column(Float, nullable=True)
    item_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    item_index: Mapped[int | None] = mapped_column(Integer, nullable=True, server_default=text("0"))
    itemvalor: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    item_codigo: Mapped[str | None] = mapped_column(Text, nullable=True)
    item_produto_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    item_descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    item_quantidade: Mapped[int | None] = mapped_column(Integer, nullable=True)
    item_desconto: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    item_comissao_base: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    item_comissao_valor: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    categoria_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    categoria_nome: Mapped[str | None] = mapped_column(Text, nullable=True)
    em_andamento_data: Mapped[date | None] = mapped_column(Date, nullable=True)
    verificado: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, server_default=text("false")
    )
    taxas_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    amazon_taxas_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    amazon_lookup_applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
