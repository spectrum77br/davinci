"""Mudanças no próprio sistema pedidas pelo Claude (migration 0493).

Eduardo, 17/11/2026: o chefe quer fazer correções rápidas no DaVinci pelo
Claude dele (conector MCP, ferramenta `pedir_mudanca`). Um robô no Mac do
Eduardo (apps/mudancas-robo, Claude Code sem tela) faz a mudança numa cópia
separada do código e roda os testes; SÓ os aprovadores (Eduardo e Thorfinn)
veem o resultado na tela Sistema › Mudanças e aprovam; aprovado, o robô
publica com o `davinci-deploy` do servidor.

  • `sistema_mudanca` — um pedido, do "na fila" ao "no ar". Escrita quase toda
    pelo robô e já é a trilha (quem pediu, quem aprovou/recusou e quando): fica
    FORA do Histórico (`historico/sql.EXCLUIDAS`) — o diff inteiro seria
    copiado a cada passo.
  • `sistema_mudanca_aprovador` — quem pode ver a tela e aprovar, por
    `user_id` (o nome e o e-mail de um usuário mudam pela tela de Usuários —
    a lista nunca é comparada por nome em tempo de execução, igual ao
    `historico_acesso`). Nasce na migration com heisenberg e thorfinn.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# A vida de um pedido. "fazendo" e "publicando" são do robô (com prazo: se ele
# parar no meio, a próxima consulta devolve o pedido); "pronta" espera uma
# pessoa; "publicada", "recusada", "precisa_equipe" e "falhou" terminam.
STATUS_MUDANCA = (
    "pedida",
    "fazendo",
    "pronta",
    "aprovada",
    "publicando",
    "publicada",
    "recusada",
    "precisa_equipe",
    "falhou",
    # pergunta (0497): o robô leu o código e respondeu direto a quem perguntou
    "respondida",
)
# Pedido de mudança (M-N) ou pergunta sobre como o sistema funciona (P-N, 0497:
# o robô só LÊ o código e a resposta vai direto a quem perguntou, sem aprovação).
TIPOS_MUDANCA = ("mudanca", "pergunta")


def _in(coluna: str, valores: tuple[str, ...]) -> str:
    return f"{coluna} IN ({', '.join(repr(v) for v in valores)})"


class SistemaMudanca(Base):
    __tablename__ = "sistema_mudanca"
    __table_args__ = (
        CheckConstraint(_in("status", STATUS_MUDANCA), name="status"),
        CheckConstraint(_in("tipo", TIPOS_MUDANCA), name="tipo"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    # O número que as pessoas falam ("M-12").
    numero: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), unique=True, nullable=False
    )
    pedido_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    tipo: Mapped[str] = mapped_column(
        String(20), nullable=False, default="mudanca", server_default=text("'mudanca'")
    )
    titulo: Mapped[str] = mapped_column(String(200), nullable=False)
    descricao: Mapped[str] = mapped_column(Text, nullable=False)
    onde: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pedida", server_default=text("'pedida'"), index=True
    )
    # O que o robô fez (português, para quem aprova), a saída dos testes, o
    # diff inteiro e o sha256 dele (a aprovação vale para ESTE diff), os
    # arquivos ([{caminho, mais, menos}]), a branch e o commit.
    resumo: Mapped[str | None] = mapped_column(Text, nullable=True)
    testes: Mapped[str | None] = mapped_column(Text, nullable=True)
    diff: Mapped[str | None] = mapped_column(Text, nullable=True)
    diff_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    arquivos: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    branch: Mapped[str | None] = mapped_column(Text, nullable=True)
    commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Pergunta: a resposta do robô (texto para quem perguntou; pode ter trechos
    # do código, nunca segredo — o robô troca por [oculto]).
    resposta: Mapped[str | None] = mapped_column(Text, nullable=True)
    # O robô: quem pegou, quando, até quando vale (renovado enquanto trabalha)
    # e quantas vezes o pedido foi pego.
    robo: Mapped[str | None] = mapped_column(String(120), nullable=True)
    pego_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    prazo_robo: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tentativas: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    # Quantas vezes a publicação aprovada foi adiada (outro deploy, disco, o
    # main com mudança de outra pessoa ainda não publicada); passou do limite,
    # o DaVinci avisa os aprovadores e quem pediu.
    adiamentos: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    aprovado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    aprovado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 0509 (24/11/2026): aprovada pela regra de `sistema_mudanca_auto_aprovacao`
    # (ninguém clicou; `aprovado_por` é quem deixou a regra).
    aprovado_automatico: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    recusado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    recusado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    motivo_recusa: Mapped[str | None] = mapped_column(Text, nullable=True)
    publicado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SistemaMudancaAprovador(Base):
    """Quem vê a tela Sistema › Mudanças e aprova (por `user_id`)."""

    __tablename__ = "sistema_mudanca_aprovador"

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SistemaMudancaAutoAprovacao(Base):
    """Pedidos de quem já nascem aprovados (0509). Eduardo, 24/11/2026: "todas
    as tarefas que o harry jogar em mudança é para fazer, está automaticamente
    aprovado". Quando o robô termina (pronta), o pedido de `pedido_por` vira
    aprovada em nome de `autorizado_por` — só enquanto quem autorizou continua
    aprovador ativo. Por `user_id`, como o aprovador."""

    __tablename__ = "sistema_mudanca_auto_aprovacao"

    pedido_por: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    autorizado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SistemaMudancaRobo(Base):
    """Presença do robô do Mac: quando ele perguntou por trabalho (ou avisou
    que está vivo) pela última vez. Tabela própria — na de presença do
    executor da Shopee ela esconderia o alarme "executor sem sinal"."""

    __tablename__ = "sistema_mudanca_robo"

    robo: Mapped[str] = mapped_column(String(120), primary_key=True)
    visto_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
