"""O conceito do vídeo autoral, esperando decisão — com o vídeo junto.

Eduardo, 23/09/2026, depois de eu insistir duas vezes no desenho errado:
"ele seguiu vindo pra roteiro sem aprovação nenhuma; antes não foi pra pedidos".

## Por que voltou

Eu tinha feito o conceito nascer como `marketing_roteiros` — primeiro ligado,
depois desligado — e argumentei que uma segunda fila seria um segundo lugar
para alguém esquecer de olhar. O argumento estava errado sobre o que importa:
mesmo desligado, o conceito entrava na LISTA de roteiros da casa sem ninguém
ter decidido nada, no meio dos briefings escritos pela equipe. A lista de
roteiros é o que a casa escreveu; texto de terceiro não entra nela por chegada.

## O que esta tabela guarda que um roteiro não guardaria

O par pedido/decisão: a justificativa de quem propôs e o motivo de quem
recusou. Um roteiro só tem o texto final — o "antes" e o "por que não"
sumiriam, e é isso que faz o mesmo pedido voltar igual na semana seguinte.

## O vídeo vem junto

`creative_id` aponta para a linha que já entrou em revisão com o arquivo. Quem
decide o conceito assiste à peça na mesma tela, em vez de decidir sobre uma
descrição. São duas perguntas diferentes sobre a mesma entrega — "este vídeo
presta?" (o `aprovado` do criativo) e "esta ideia vira briefing nosso?" (aqui)
— e as duas continuam podendo ser respondidas de formas diferentes.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

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
    # O conceito, como a agência escreveu. Vira o `texto` do roteiro no sim.
    descricao: Mapped[str] = mapped_column(Text, nullable=False)

    marca: Mapped[str | None] = mapped_column(String(120), nullable=True)
    sku: Mapped[str | None] = mapped_column(String(120), nullable=True)

    equipe: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=STATUS_PENDENTE, server_default=text("'pendente'")
    )
    motivo: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Qual persona do elenco está na peça. NULL é resposta legítima, não
    # ausência de dado: as peças de mão e produto não têm ninguém em quadro.
    personagem_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketing_personagens.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Quem está na peça quando NÃO é ninguém do elenco (migration 0330): a
    # descrição do personagem, como a agência escreveu. Preenchido só quando
    # ela escolhe "outro personagem" — e aí é obrigatório, porque sem o texto
    # essa escolha ficaria idêntica a "sem persona". É o caso que mais pesa
    # para a casa: personagem que não passou pela aprovação de procedência.
    personagem_outro: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A entrega que trouxe este conceito. SET NULL: apagar o vídeo não apaga o
    # registro de que a agência propôs e a casa decidiu.
    creative_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketing_creatives.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
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
