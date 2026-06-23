"""BlingEnvioEvento — ledger de envios por evento (entrada em situação 15).

Append-only. Cada linha = um item de pedido (grão de bling_orders) no instante
em que o pedido ENTROU na situação 15 ("em andamento"). Populado por trigger de
banco (migration 0156), não pela aplicação — por isso não há writes daqui.

Campos:
  - occurred_at: instante observado da transição (imutável; congela o dia).
  - shipping_day: dia-envio ancorado nas 08:00 BRT (08:00 hoje → 07:59 amanhã
    pertence a "hoje"). Computado no trigger; é o que a aba Envios agrupa.

A tag de estoque NÃO é guardada: deriva-se de `item_codigo` na query, via
`app.services.sku_tags.sql_clause_for_tag`, pra nunca divergir do classifier.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class BlingEnvioEvento(Base):
    __tablename__ = "bling_envio_evento"

    bling_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    item_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_codigo: Mapped[str | None] = mapped_column(Text, nullable=True)
    numero: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"),
    )
    shipping_day: Mapped[date] = mapped_column(Date, nullable=False)
