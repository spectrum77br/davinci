from datetime import date
from uuid import UUID, uuid4

from sqlalchemy import Date, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Chamado(Base, TimestampMixin):
    """Chamado de pós-venda (caso a acompanhar). Registro manual, no formato da
    Planilha2 de atualização1.xlsx: Data | pedido bling | pedido marketplace |
    plataforma | conta | STATUS PLATAFORMA | localização.

    `meli_status` guarda a assinatura de status do Meli (os 8 campos:
    order_status, ship_status, ship_substatus, cancel_group, return_status,
    claim_stage, claim_status, benefited) que alimenta a sugestão de Status
    Bling candidato (app.services.chamados_rules.sugerir). A classificação
    final (`status_bling`) é decisão do operador — a sugestão nunca grava
    sozinha.
    """

    __tablename__ = "chamados"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    data: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    pedido_bling: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    pedido_marketplace: Mapped[str | None] = mapped_column(Text, nullable=True)
    plataforma: Mapped[str | None] = mapped_column(Text, nullable=True)
    conta: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Assinatura de status do Meli (dict dos 8 campos). Vazio pra plataformas
    # sem esse fluxo — a sugestão simplesmente não se aplica.
    meli_status: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    localizacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Classificação escolhida pelo operador (dica vem de chamados_rules.sugerir).
    status_bling: Mapped[str | None] = mapped_column(Text, nullable=True)
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
