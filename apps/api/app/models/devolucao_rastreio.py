from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, Text
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
    # --- Automático (migration 0241, Eduardo 03/09): o pacote que VOLTA ---
    # Preenchido pelo job services/devolucao_rastreio_sync a partir da returns
    # API de TikTok/Shopee/ML (contrato: services/devolucao_returns.ReturnInfo).
    # O manual acima continua mandando na aba; estes só entram quando o manual
    # está vazio. `localizacao_auto` vem do 17track (códigos Correios).
    rastreio_auto: Mapped[str | None] = mapped_column(Text, nullable=True)
    transportadora_auto: Mapped[str | None] = mapped_column(Text, nullable=True)
    localizacao_auto: Mapped[str | None] = mapped_column(Text, nullable=True)
    localizacao_auto_data: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    devolucao_status_auto: Mapped[str | None] = mapped_column(Text, nullable=True)
    devolucao_id_auto: Mapped[str | None] = mapped_column(Text, nullable=True)
    fonte_auto: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Quando a devolução foi ABERTA no marketplace → "Em devolução desde" real
    # (o backfill da 0236 carimbou 02/09 em todo mundo).
    devolucao_criada_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    devolucao_atualizada_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    auto_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # "Em devolução desde" digitado na mão (migration 0242) — vale mais que o
    # automático (devolução aberta no marketplace / carimbo da Logística /
    # entrada em 83957). Caso 287144: entrou em 19/08 pela Viena no Bling.
    entrada_manual: Mapped[date | None] = mapped_column(Date, nullable=True)
    updated_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
