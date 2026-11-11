"""Jurídico — os processos judiciais num lugar só (Ouvidoria › Jurídico).

Vinicius, 11/11/2026: "vamos criar um painel jurídico … no padrão da conciliação ou
chamado … data, número do processo, origem (veio de denúncia, veio da logística,
incluímos à mão), última movimentação, Jusbrasil, beneficiário, contra quem" + o
advogado na tela. Escolheu a Opção B (com Assunto, Situação e Raio-X); no mesmo dia o
Próximo prazo saiu ("esse prazo pode tirar") e entrou o Pedido / referência.

Uma linha por processo. Os casos da Denúncia que foram ao advogado entram sozinhos
(`denuncia_caso_id`), e também os chamados encaminhados ao jurídico
(`chamado_id`, origem Logística). O resto entra à mão pelo "+ novo processo".
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

ORIGENS = ("denuncia", "logistica", "manual")
LADOS = ("autor", "reu")
SITUACOES = ("aguardando_numero", "em_andamento", "audiencia", "sentenca", "encerrado")


class JuridicoProcesso(Base):
    __tablename__ = "juridico_processos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # 'denuncia' | 'logistica' | 'manual'
    origem: Mapped[str] = mapped_column(Text, nullable=False)
    denuncia_caso_id: Mapped[int | None] = mapped_column(BigInteger)
    chamado_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    # Pedido / referência (11/11, Vinicius: "número pedido ou número referência"): o pedido do
    # Bling (Logística) ou o que digitarem (Manual). Na Denúncia a referência é o caso (Caso 006),
    # que a tela mostra a partir de denuncia_caso_id — aqui fica vazio.
    pedido: Mapped[str | None] = mapped_column(Text)
    # a coluna Data: quando foi encaminhado ao jurídico
    encaminhado_em: Mapped[date | None] = mapped_column(Date)
    numero: Mapped[str | None] = mapped_column(Text)
    # de que lado nós estamos: 'autor' (nós entramos) | 'reu' (entraram contra nós)
    lado: Mapped[str] = mapped_column(Text, nullable=False, server_default="autor")
    autor: Mapped[str | None] = mapped_column(Text)
    autor_obs: Mapped[str | None] = mapped_column(Text)
    reu: Mapped[str | None] = mapped_column(Text)
    reu_obs: Mapped[str | None] = mapped_column(Text)
    advogado: Mapped[str | None] = mapped_column(Text)
    advogado_escritorio: Mapped[str | None] = mapped_column(Text)
    assunto: Mapped[str | None] = mapped_column(Text)
    # 'aguardando_numero' | 'em_andamento' | 'audiencia' | 'sentenca' | 'encerrado'
    situacao: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="aguardando_numero"
    )
    vara: Mapped[str | None] = mapped_column(Text)
    valor_causa: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    # link de consulta no Jusbrasil; a leitura automática das movimentações vem depois
    jusbrasil_link: Mapped[str | None] = mapped_column(Text)
    observacao: Mapped[str | None] = mapped_column(Text)
    criado_por: Mapped[str | None] = mapped_column(Text)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    atualizado_por: Mapped[str | None] = mapped_column(Text)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "uq_juridico_processos_caso",
            "denuncia_caso_id",
            unique=True,
            postgresql_where=text("denuncia_caso_id IS NOT NULL"),
        ),
        Index(
            "uq_juridico_processos_chamado",
            "chamado_id",
            unique=True,
            postgresql_where=text("chamado_id IS NOT NULL"),
        ),
    )


class JuridicoMovimentacao(Base):
    """Andamento do processo. Hoje é digitado; quando o Jusbrasil for ligado, `fonte` =
    'jusbrasil'. A coluna "Última movimentação" é a mais nova (data, id)."""

    __tablename__ = "juridico_movimentacoes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    processo_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("juridico_processos.id", ondelete="CASCADE"), nullable=False
    )
    data: Mapped[date] = mapped_column(Date, nullable=False)
    texto: Mapped[str] = mapped_column(Text, nullable=False)
    # 'manual' | 'jusbrasil' | 'davinci'
    fonte: Mapped[str] = mapped_column(Text, nullable=False, server_default="manual")
    criado_por: Mapped[str | None] = mapped_column(Text)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_juridico_movimentacoes_processo", "processo_id"),)
