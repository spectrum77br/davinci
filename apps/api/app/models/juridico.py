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

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    func,
    text,
)
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
    """Andamento do processo. A coluna "Última movimentação" é a mais nova (data, id).

    `fonte`: 'manual' (digitada) ou 'djen' — 11/11/2026, o Vigia do Jurídico lê todo dia no
    Diário de Justiça Eletrônico Nacional (CNJ) as publicações de cada processo com nº
    (intimação, decisão, citação, distribuição) e lança aqui; `externo_id` (id da comunicação no
    DJEN) não deixa lançar duas vezes."""

    __tablename__ = "juridico_movimentacoes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    processo_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("juridico_processos.id", ondelete="CASCADE"), nullable=False
    )
    data: Mapped[date] = mapped_column(Date, nullable=False)
    texto: Mapped[str] = mapped_column(Text, nullable=False)
    # 'manual' | 'djen'
    fonte: Mapped[str] = mapped_column(Text, nullable=False, server_default="manual")
    externo_id: Mapped[str | None] = mapped_column(Text)
    # do DJEN: órgão que publicou, tipo (Intimação, Lista de distribuição…), o texto inteiro
    # (sem HTML) e o link do documento no tribunal
    orgao: Mapped[str | None] = mapped_column(Text)
    tipo: Mapped[str | None] = mapped_column(Text)
    texto_completo: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(Text)
    criado_por: Mapped[str | None] = mapped_column(Text)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_juridico_movimentacoes_processo", "processo_id"),
        Index(
            "uq_juridico_movimentacoes_externo",
            "processo_id",
            "externo_id",
            unique=True,
            postgresql_where=text("externo_id IS NOT NULL"),
        ),
    )


class JuridicoLeitura(Base):
    """Cada vez que alguém leu o Diário oficial (DJEN) pelos processos: o Mac mini às 07:30
    (`origem` 'mac_mini') ou o botão "Buscar no Diário" (`origem` 'navegador', `por` = quem).
    O topo do painel mostra a última."""

    __tablename__ = "juridico_leituras"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    quando: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    origem: Mapped[str] = mapped_column(Text, nullable=False)
    por: Mapped[str | None] = mapped_column(Text)
    processos: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    publicacoes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    novas: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    falhas: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    erro: Mapped[str | None] = mapped_column(Text)


class JuridicoPedido(Base):
    """Pedido de leitura ao Mac mini (11/11/2026, Vinicius: "6 da manhã … chegar e ver tudo
    pronto" + "no botão Atualizar, atualizar Jusbrasil e CNJ junto"). O DaVinci cria às 06:00
    (`origem` 'cron') e no botão Atualizar ('botao', no máximo um Jusbrasil a cada 2 h); o vigia
    do mini pega (`pego_em`), lê o Diário (CNJ) e — se `jusbrasil` — o Jusbrasil no perfil 50,
    devagar, e vai contando o `progresso` até `terminado_em`."""

    __tablename__ = "juridico_pedidos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pedido_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    pedido_por: Mapped[str | None] = mapped_column(Text)
    # 'cron' | 'botao'
    origem: Mapped[str] = mapped_column(Text, nullable=False)
    jusbrasil: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    pego_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    progresso: Mapped[str | None] = mapped_column(Text)
    terminado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ok: Mapped[bool | None] = mapped_column(Boolean)
    resultado: Mapped[str | None] = mapped_column(Text)

