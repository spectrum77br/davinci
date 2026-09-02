from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class DevolucaoRastreio(Base, TimestampMixin):
    """Rastreio do pacote de UM pedido em devolução (aba Acompanhamento).

    Grão de PEDIDO (`bling_orders.numero` — o espelho bling_orders tem uma
    linha por ITEM, por isso sem FK). Preenchimento manual pelo operador:
    código de rastreio e última localização vista no site da transportadora.
    `localizacao_data` é carimbada automaticamente quando a localização muda —
    é a "data da última movimentação" da folha do Eduardo (2026-09-02), usada
    depois pra detectar pacote parado há N dias.
    """

    __tablename__ = "devolucao_rastreio"

    pedido_bling: Mapped[str] = mapped_column(Text, primary_key=True)
    rastreio: Mapped[str | None] = mapped_column(Text, nullable=True)
    localizacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    localizacao_data: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
