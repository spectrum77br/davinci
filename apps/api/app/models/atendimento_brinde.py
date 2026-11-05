"""O brinde do pedido não pago: o que aconteceu com ele (migration 0455, 05/11/2026).

O dono, 05/11/2026: 3 h depois de um pedido criado e não finalizado, a oferta
(celular ganha AirTag, mala ganha chaveiro); se a pessoa comprar, um botão no
Comunicador põe o brinde no pedido. Quem tem o brinde prometido é a linha
`enviado` do motor em `atendimento_automacao_registros` (`AUTOMACOES_BRINDE`):
esta tabela SÓ NASCE quando alguém age ou quando o prazo vence, uma linha por
loja e pedido.

`estado`: adicionado (está no pedido do Bling) | dispensado (a pessoa escolheu
não pôr) | expirado (12 h sem ninguém agir: a NF seguiu sem ele) | incerto (o
Bling não respondeu ao PUT: a próxima ação começa pelo GET, nunca repete o PUT
às cegas). Os três primeiros soltam a NF (`services/atendimento/brinde.py`).

Nenhum dado de cliente: só loja, número do pedido, SKU, estado e quem clicou.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AtendimentoBrinde(Base, TimestampMixin):
    """O brinde de um pedido, depois que alguém agiu ou que o prazo venceu."""

    __tablename__ = "atendimento_brindes"
    __table_args__ = (
        UniqueConstraint("integration_id", "pedido", name="uq_atendimento_brindes_pedido"),
        CheckConstraint(
            "estado IN ('adicionado', 'dispensado', 'expirado', 'incerto')",
            name="estado",
        ),
        # Os alertas da lista: quem expirou nas últimas horas.
        Index("ix_atendimento_brindes_estado_em", "estado", "em"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    integration_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False
    )
    # O nº na plataforma (o `numeroloja` do Bling), não o nº do Bling.
    pedido: Mapped[str] = mapped_column(String(64), nullable=False)
    # "airtag" | "chaveiro" (`constantes.BRINDE_SKU`).
    tipo: Mapped[str | None] = mapped_column(String(16), nullable=True)
    sku: Mapped[str | None] = mapped_column(String(64), nullable=True)
    estado: Mapped[str] = mapped_column(String(16), nullable=False)
    bling_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Quem agiu; NULL = o prazo venceu sozinho.
    por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Sem texto de cliente: a frase do Bling ou o porquê do `incerto`.
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)
