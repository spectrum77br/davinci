"""StockMovement — append-only journal of Bling estoque webhook events.

One row per webhook. `operacao` ("E" entrada / "S" saida) and `quantidade`
come straight from the payload, so we don't have to compute a stock-diff
on the fly. `saldo_fisico` / `saldo_virtual` are the totals AFTER the
movement (also from the payload) so the operador can reconcile against
the running balance without re-querying Bling. `origem` is filled lazily
for saídas by cross-referencing bling_orders by SKU + date.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, Integer, Numeric, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class StockMovement(Base, TimestampMixin):
    __tablename__ = "stock_movements"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    bling_product_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    sku: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    product_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # 'E' (entrada) or 'S' (saida) — stored as 1-char text since Bling
    # only emits these two; an enum would buy nothing.
    tipo: Mapped[str] = mapped_column(Text, nullable=False)
    quantidade: Mapped[int] = mapped_column(Integer, nullable=False)
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    origem: Mapped[str | None] = mapped_column(Text, nullable=True)
    saldo_fisico: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    saldo_virtual: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
