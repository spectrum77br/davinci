"""Flex (ML Envios Flex / Shopee Entrega Direta) — migration 0361.

Procedimento: /Users/admmarketing/Downloads/procedimento-flex.md; análise com
os fatos das APIs: relatorios/Flex_analise_02-10-2026.md.

Três tabelas, todas escritas pela MÁQUINA (por isso fora do Histórico, ver
historico/sql.EXCLUIDAS):

  • `flex_pedido` — o pedido que a plataforma diz que sai pelo Flex. Nasce no
    `marketplace_shipment_check` (roda de minuto em minuto nos pedidos em
    aberto) com o envio que ele já lê — sem chamada extra. É dele que sai o
    "saldo Flex" da família: o .sp livre MENOS os pedidos Flex que ainda não
    foram baixados no .sp (`no_sp = false`). Pedido Flex SEMPRE sai do .sp.
  • `flex_anuncio_estado` — UMA linha por anúncio (conta + anúncio, não por
    variação: o Flex do ML e o da Shopee valem para o anúncio inteiro). Guarda
    o que a regra quer (`desejado`), o que a plataforma diz que está
    (`observado`) e a fila de escrita (tentativas, próxima tentativa, erro).
    Comparar só com a última decisão do DaVinci não basta: o estado muda fora
    daqui (capacidade estourada, o vendedor no painel, a Shopee tirando a loja
    do canal por uma semana).
  • `flex_log` — só inserção: cada decisão/escrita com o estado de antes e o
    de depois, para poder desfazer (como a troca de SKU).

Os valores fechados são TEXT com CHECK — nada de enum do Postgres (valor novo
em enum pede migration e não volta atrás).
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Valores fechados (espelhados nos CHECK da migration 0361).
FLEX_PLATAFORMAS = ("ml", "shopee")
FLEX_DESEJADO = ("ligado", "desligado", "inelegivel")
FLEX_OBSERVADO = ("ligado", "desligado")
FLEX_MODOS = ("desligado", "observar", "piloto", "ativo")
# `pedido_sp` / `pedido_sem_sp`: o robô de prioridade levou o pedido Flex para
# o .sp / o .sp não cobria e ficou o aviso (etapa 2, services/prioridade_estoque).
# `decidir`: a regra mudou o que quer para o anúncio (etapa 3, flex_motor) —
# só a MUDANÇA vira linha, não cada rodada.
FLEX_ACOES = (
    "decidir",
    "ligar",
    "desligar",
    "pedir_aprovacao",
    "aprovar",
    "emergencia",
    "ler",
    "pedido_sp",
    "pedido_sem_sp",
)
FLEX_RESULTADOS = ("ok", "erro", "simulado", "pendente", "ignorado")


def _in(coluna: str, valores: tuple[str, ...]) -> str:
    """Texto do CHECK. NULL passa (NULL IN (...) não é falso): a coluna que
    aceita vazio continua aceitando — quem proíbe vazio é o NOT NULL."""
    lista = ", ".join(f"'{v}'" for v in valores)
    return f"{coluna} IN ({lista})"


class FlexPedido(Base):
    """Pedido de venda cujo envio é Flex, por `bling_id` (um pedido do Bling)."""

    __tablename__ = "flex_pedido"
    __table_args__ = (CheckConstraint(_in("plataforma", FLEX_PLATAFORMAS), name="plataforma"),)

    bling_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    plataforma: Mapped[str] = mapped_column(Text, nullable=False)
    # Conta do pedido. SET NULL: a linha continua valendo para o saldo do .sp
    # mesmo se a integração for apagada.
    integration_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("integrations.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Número do pedido no marketplace (o `numeroloja` do Bling).
    numeroloja: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Valor cru da plataforma: ML `self_service`; Shopee "90022 · Shopee
    # Entrega Direta" (canal e transportadora). Ver services/flex_envio.
    envio_tipo: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "Despachar até" (o mesmo prazo que o shipment check grava em
    # bling_orders.marketplace_ship_deadline).
    prazo: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    detectado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Todos os itens do pedido já estão no lote .sp (o Bling baixa de São
    # Bernardo). Falso = o pedido ainda reserva em outro lote e tem que ser
    # descontado do saldo Flex. Escrito pelo shipment check (a partir do
    # espelho) e pelo robô de prioridade logo depois de trocar para o .sp.
    no_sp: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # Aviso para a operação: o .sp não cobre o pedido Flex e o robô de
    # prioridade NÃO o trocou de lote (services/prioridade_estoque). Volta a
    # NULL quando o pedido vai para o .sp.
    alerta: Mapped[str | None] = mapped_column(Text, nullable=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FlexAnuncioEstado(Base):
    """Estado do Flex de UM anúncio (conta + anúncio)."""

    __tablename__ = "flex_anuncio_estado"
    __table_args__ = (
        CheckConstraint(_in("plataforma", FLEX_PLATAFORMAS), name="plataforma"),
        CheckConstraint(_in("desejado", FLEX_DESEJADO), name="desejado"),
        CheckConstraint(_in("observado", FLEX_OBSERVADO), name="observado"),
        # A tela lista só os que esperam uma pessoa: índice minúsculo.
        Index(
            "ix_flex_anuncio_estado_aguardando",
            "integration_id",
            postgresql_where=text("aguardando_aprovacao"),
        ),
    )

    integration_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("integrations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Id do anúncio na plataforma (ML `MLB…`, Shopee `item_id`) — o mesmo
    # `product_links.external_id`.
    external_id: Mapped[str] = mapped_column(Text, primary_key=True)
    plataforma: Mapped[str] = mapped_column(Text, nullable=False)
    # O que a regra quer: ligado | desligado | inelegivel (+ o porquê).
    desejado: Mapped[str] = mapped_column(Text, nullable=False)
    motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    # O que a plataforma disse da última vez que foi lida (NULL = não lido).
    observado: Mapped[str | None] = mapped_column(Text, nullable=True)
    observado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # LIGAR espera uma pessoa aprovar na tela (orientação do ML: a ativação
    # deve ser decisão deliberada do vendedor). Desligar não espera.
    aguardando_aprovacao: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    aprovado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    aprovado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    aplicado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tentativas: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    proxima_tentativa: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ultimo_erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A plataforma RECUSOU ligar (ML 403 "item down" / 404; Shopee sem o canal
    # no anúncio): o motor não tenta ligar de novo — a regra vira `inelegivel`
    # com este motivo até uma pessoa aprovar outra vez (aprovar limpa). Só
    # trava o LIGAR: desligar segue valendo (é o lado seguro).
    recusa: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Menor saldo Flex entre as famílias do anúncio e quais famílias são
    # (texto, "dg053,dg054") — o que a tela mostra ao lado da decisão.
    saldo_sp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    familias: Mapped[str | None] = mapped_column(Text, nullable=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FlexLog(Base):
    """Trilha do Flex: só inserção, uma linha por decisão/escrita."""

    __tablename__ = "flex_log"
    __table_args__ = (
        CheckConstraint(_in("plataforma", FLEX_PLATAFORMAS), name="plataforma"),
        CheckConstraint(_in("acao", FLEX_ACOES), name="acao"),
        CheckConstraint(_in("estado_antes", FLEX_DESEJADO), name="estado_antes"),
        CheckConstraint(_in("estado_depois", FLEX_DESEJADO), name="estado_depois"),
        CheckConstraint(_in("modo", FLEX_MODOS), name="modo"),
        CheckConstraint(_in("resultado", FLEX_RESULTADOS), name="resultado"),
        Index("ix_flex_log_anuncio", "integration_id", "external_id", "criado_em"),
        Index("ix_flex_log_criado_em", "criado_em"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Sem FK de propósito: o log sobrevive à conta apagada.
    integration_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    external_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    plataforma: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Pedido do Bling (as ações `pedido_*`): sem FK, como a conta.
    bling_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sku: Mapped[str | None] = mapped_column(Text, nullable=True)
    saldo_sp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    acao: Mapped[str] = mapped_column(Text, nullable=False)
    estado_antes: Mapped[str | None] = mapped_column(Text, nullable=True)
    estado_depois: Mapped[str | None] = mapped_column(Text, nullable=True)
    modo: Mapped[str] = mapped_column(Text, nullable=False)
    resultado: Mapped[str] = mapped_column(Text, nullable=False)
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    # O porquê da decisão (`decidir`) ou da escrita, em texto para a tela.
    motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Quem fez: a pessoa (aprovar, emergência, sincronizar pela tela). NULL =
    # o robô. Sem FK, como a conta: o log sobrevive ao usuário apagado.
    por: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
