"""BlingEnvioCorrecao — fila (outbox) de correções de dia de envio.

Quando um pedido já contado no ledger (bling_envio_evento) entra de novo na
situação 15 vindo de um estado NÃO-enviado (o operador errou, voltou pra "em
aberto" e relançou noutro dia), o trigger re-carimba o `shipping_day` pro dia
da correção E grava uma linha aqui. Uma rotina externa (Claude Code, que já
tem o Threema) drena `threema_sent_at IS NULL`, avisa o destinatário e marca
como enviado.

Chave (bling_id, dia_anterior, dia_novo): dedup natural — pedido multi-item
gera UM aviso só por movimento de dia. Populada por trigger (migration 0157),
não pela aplicação.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class BlingEnvioCorrecao(Base):
    __tablename__ = "bling_envio_correcao"

    bling_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    dia_anterior: Mapped[date] = mapped_column(Date, primary_key=True)
    dia_novo: Mapped[date] = mapped_column(Date, primary_key=True)
    numero: Mapped[str | None] = mapped_column(Text, nullable=True)
    item_codigo: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"),
    )
    threema_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
