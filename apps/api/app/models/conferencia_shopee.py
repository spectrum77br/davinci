"""Conferência Shopee — relatório semanal de marketing por loja (migration 0377).

Terça (semana fechada) e quinta (parcial seg–qua) às 13:30 BRT o worker cria
uma EXECUÇÃO com uma COLETA por loja ativa; o executor do Mac abre cada loja
pelo perfil do AdsPower, lê as APIs internas da Central do Vendedor e devolve
os números (ColetaDados, docs/conferencia-shopee.md). Quando a última coleta
chega (ou no prazo), o servidor calcula o relatório (services/
conferencia_shopee/calculo) e o congela na execução.

  • `conferencia_shopee_conta` — a lista de lojas (perfil do AdsPower, nome
    exibido, grupo Mala/Celular). Editada por PESSOAS na tela: fica COM o
    gatilho do Histórico.
  • `conferencia_shopee_execucao` — uma rodada: as 4 semanas fixadas na
    criação, os prazos e o relatório congelado.
  • `conferencia_shopee_coleta` — uma loja numa rodada: a fila do executor
    (status, tentativas, adiamentos) e os números crus (`dados`).
  • `conferencia_shopee_saldo` — o saldo de Ads lido em cada coleta. A Shopee
    só mostra o saldo do momento: o "saldo da semana anterior" sai daqui.

As três últimas são da MÁQUINA (fora do Histórico, historico/sql.EXCLUIDAS).
Valores fechados em TEXT com CHECK — nada de enum do Postgres.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Valores fechados (espelhados nos CHECK da migration 0377).
CONFERENCIA_GRUPOS = ("mala", "celular")
CONFERENCIA_TIPOS = ("semanal", "parcial")
CONFERENCIA_ORIGENS = ("agenda", "manual")
CONFERENCIA_EXECUCAO_STATUS = ("coletando", "pronto", "cancelado")
# pendente/coletando = na fila; o resto é final. `expirada` só o varredor do
# servidor marca (passou do prazo sem o executor voltar).
CONFERENCIA_COLETA_STATUS = (
    "pendente",
    "coletando",
    "ok",
    "parcial",
    "deslogada",
    "perfil_em_uso",
    "sem_automacao",
    "bloqueada",
    "interrompida",
    "erro",
    "expirada",
)


def _in(coluna: str, valores: tuple[str, ...]) -> str:
    lista = ", ".join(f"'{v}'" for v in valores)
    return f"{coluna} IN ({lista})"


def _uuid_pk() -> Mapped[UUID]:
    return mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )


class ConferenciaShopeeConta(Base):
    """Uma loja Shopee da conferência. A chave é o id do perfil no AdsPower
    (`k1d…`): o número do perfil muda quando o AdsPower renumera, o id não."""

    __tablename__ = "conferencia_shopee_conta"
    __table_args__ = (CheckConstraint(_in("grupo", CONFERENCIA_GRUPOS), name="grupo"),)

    id: Mapped[UUID] = _uuid_pk()
    adspower_user_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    # Nome exibido no relatório (KIA aparece como Fiore, JLAS como Atlas).
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    # mala | celular. Eletro não é grupo de conta: é a parte eletro de cada
    # conta de celular, separada produto a produto no cálculo.
    grupo: Mapped[str] = mapped_column(Text, nullable=False)
    ativo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    ordem: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    # `store_info.account_name` em minúsculas: acha a loja e a integração do
    # DaVinci (vínculos dos anúncios → o que é eletro). NULL = sem vínculo.
    conta_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ConferenciaShopeeExecucao(Base):
    """Uma rodada da conferência. As semanas e os prazos são fixados na
    criação: uma coleta atrasada continua cobrindo o mesmo período."""

    __tablename__ = "conferencia_shopee_execucao"
    __table_args__ = (
        CheckConstraint(_in("tipo", CONFERENCIA_TIPOS), name="tipo"),
        CheckConstraint(_in("origem", CONFERENCIA_ORIGENS), name="origem"),
        CheckConstraint(_in("status", CONFERENCIA_EXECUCAO_STATUS), name="status"),
        Index("ix_conferencia_shopee_execucao_criado_em", text("criado_em DESC")),
    )

    id: Mapped[UUID] = _uuid_pk()
    tipo: Mapped[str] = mapped_column(Text, nullable=False)
    origem: Mapped[str] = mapped_column(Text, nullable=False)
    # E-mail de quem apertou "Gerar agora"; NULL = agenda.
    criado_por: Mapped[str | None] = mapped_column(Text, nullable=True)
    # [{"inicio": "YYYY-MM-DD", "fim": "YYYY-MM-DD"}] × 4; 0 = S1 (a do
    # relatório), 3 = S4 (a mais antiga).
    semanas: Mapped[list] = mapped_column(JSONB, nullable=False)
    # Último dia cujos afiliados têm de estar publicados (= S1.fim).
    afiliados_ate: Mapped[date] = mapped_column(Date, nullable=False)
    # Até quando o executor espera a Shopee publicar os afiliados de ontem.
    esperar_afiliados_ate: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Depois disto nenhuma loja nova começa (às 18h o robô de Ads usa os perfis).
    corte: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # O varredor marca o que sobrou como `expirada` e fecha o relatório.
    prazo: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="coletando", server_default=text("'coletando'")
    )
    # Relatório congelado (calculo.montar_relatorio, versao 1). NULL enquanto
    # coleta; "recalcular" refaz a partir dos `dados` guardados.
    relatorio: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    finalizado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Carimbo do aviso no Threema: manda uma vez só, mesmo com reinício.
    threema_enviado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ConferenciaShopeeColeta(Base):
    """Uma loja numa rodada: item da fila do executor e os números crus."""

    __tablename__ = "conferencia_shopee_coleta"
    __table_args__ = (
        CheckConstraint(_in("status", CONFERENCIA_COLETA_STATUS), name="status"),
        Index("ix_conferencia_shopee_coleta_execucao_id", "execucao_id"),
        Index("ix_conferencia_shopee_coleta_status_disponivel_apos", "status", "disponivel_apos"),
    )

    id: Mapped[UUID] = _uuid_pk()
    execucao_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "conferencia_shopee_execucao.id",
            ondelete="CASCADE",
            name="fk_conferencia_shopee_coleta_execucao",
        ),
        nullable=False,
    )
    # SET NULL: a loja apagada da lista não leva junto o histórico; os nomes
    # abaixo são a foto do momento da rodada.
    conta_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "conferencia_shopee_conta.id",
            ondelete="SET NULL",
            name="fk_conferencia_shopee_coleta_conta",
        ),
        nullable=True,
    )
    adspower_user_id: Mapped[str] = mapped_column(Text, nullable=False)
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    grupo: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pendente", server_default=text("'pendente'")
    )
    tentativas: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    # Quantas vezes voltou para a fila sem gastar tentativa (perfil em uso,
    # afiliados ainda não publicados).
    adiamentos: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    # Só os de perfil em uso: é esta que tem o limite de 3 (esperar os
    # afiliados a manhã toda não gasta as voltas de quem achou o perfil aberto).
    adiamentos_perfil: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    disponivel_apos: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    concluido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    agente: Mapped[str | None] = mapped_column(Text, nullable=True)
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ColetaDados versao 1 (docs/conferencia-shopee.md §4).
    dados: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ConferenciaShopeeSaldo(Base):
    """Saldo de Ads lido numa coleta. Sem FK de propósito: o histórico do saldo
    sobrevive à execução apagada."""

    __tablename__ = "conferencia_shopee_saldo"
    __table_args__ = (
        Index(
            "ix_conferencia_shopee_saldo_adspower_user_id_lido_em", "adspower_user_id", "lido_em"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    adspower_user_id: Mapped[str] = mapped_column(Text, nullable=False)
    lido_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    execucao_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
