"""EstoqueDiaFinalizado — admin "fechou" o dia na aba Envios.

Quando o admin tica CONFERIDO=true pra um dia em section='envio'
(toggle_estoque_check), gravamos uma linha aqui pra travar o badge
`conferencia_estoque` daquele dia como "total" — independente de
quantos produtos novos entrarem em tags depois.

Sem isso, o badge regredia de "total" → "parcial" sempre que
entrasse um produto novo na tag (porque o total_produtos current
era comparado com o estoque_conferidos histórico).
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class EstoqueDiaFinalizado(Base):
    __tablename__ = "estoque_dia_finalizado"

    data: Mapped[date] = mapped_column(Date, primary_key=True)
    finalizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"),
    )
