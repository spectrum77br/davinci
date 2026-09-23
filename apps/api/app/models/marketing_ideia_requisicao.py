"""Ideia proposta pela agência — ela propõe, a casa libera.

Eduardo, 23/09/2026: "deixa eles publicarem também em alguma parte um vídeo
100% feito por eles, descrição tudo, caso eles queiram essa liberdade; daí vira
pedidos no DaVinci que deve a nossa aprovação pra eles poderem gerar o que
quiserem".

## Por que não é um roteiro com `ativo=False`

Porque as duas coisas significam coisas diferentes, e misturá-las apagaria a
diferença. `marketing_roteiros.ativo=False` quer dizer "briefing DA CASA ainda
não liberado" — é o interruptor de revisão do que a casa escreveu. Uma proposta
da agência é o oposto: o texto é dela, e o que falta não é publicação, é
PERMISSÃO. Enfiar as duas no mesmo campo faria a lista interna misturar ideia
que a casa ainda não soltou com ideia que a casa ainda não aceitou.

Some-se a isto o que a versão de roteiro já ensinou: sem tabela própria, o
"antes" some. Aqui o antes é o pedido — com justificativa, e com o motivo da
recusa quando houver. É esse par que evita o mesmo pedido voltar igual.

## O que acontece no sim

A aprovação CRIA um `MarketingRoteiro` endereçado à agência que pediu
(`equipe_destino = equipe`), ligado de volta por `roteiro_id`. A partir daí a
peça segue o caminho normal: a agência lê, produz e entrega por ela. Ou seja, o
sim não é um carimbo — é o que transforma a proposta em briefing de verdade.

O ARQUIVO não sobe aqui. Aprovar libera a agência a PRODUZIR; o vídeo entra
depois, pela entrega, e continua passando pelo `aprovado` do criativo. São dois
vistos distintos: um na ideia, outro na peça.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# Mesma fila curta da requisição de personagem: ou espera, ou foi decidida.
STATUS_PENDENTE = "pendente"
STATUS_APROVADA = "aprovada"
STATUS_RECUSADA = "recusada"


class MarketingIdeiaRequisicao(Base, TimestampMixin):
    __tablename__ = "marketing_ideia_requisicoes"
    __table_args__ = (
        Index("ix_marketing_ideia_requisicoes_fila", "status", "created_at"),
        Index("ix_marketing_ideia_requisicoes_equipe", "equipe"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    titulo: Mapped[str] = mapped_column(String(160), nullable=False)
    # A ideia inteira, do jeito que a agência escreveu: cena, tom, batidas.
    # Vira o `texto` do roteiro quando alguém aprova.
    descricao: Mapped[str] = mapped_column(Text, nullable=False)
    # Por que vale a pena. Não vira roteiro — fica só no pedido, porque é
    # argumento para quem decide, não instrução para quem produz.
    justificativa: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Descem para o roteiro na aprovação. Texto livre de propósito: quem resolve
    # marca e SKU é o servidor, pelo mesmo caminho do roteiro escrito à mão.
    marca: Mapped[str | None] = mapped_column(String(120), nullable=True)
    sku: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Mesma string de equipe que o token do portal carrega.
    equipe: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=STATUS_PENDENTE, server_default=text("'pendente'")
    )
    motivo: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Preenchido no sim: o rastro de que este pedido virou aquele briefing.
    roteiro_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketing_roteiros.id", ondelete="SET NULL"),
        nullable=True,
    )
    decidido_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decidido_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
