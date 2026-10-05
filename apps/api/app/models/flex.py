"""Flex (ML Envios Flex / Shopee Entrega Direta) — migration 0364.

Procedimento: /Users/admmarketing/Downloads/procedimento-flex.md; análise com
os fatos das APIs: relatorios/Flex_analise_02-10-2026.md.

Cinco tabelas, todas escritas pela MÁQUINA (por isso fora do Histórico, ver
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
  • `flex_conta` — a conta PODE ter Flex? (ML: assinatura do Flex em
    `/flex/sites/MLB/users/{id}/subscriptions/v1`; Shopee: o canal Entrega
    Direta ligado na LOJA, `get_channel_list`) — guardado ~1 h para não
    perguntar a cada rodada — e a última descoberta dos anúncios da conta
    (todos os ids do ML, para achar o anúncio que o DaVinci não conhece).
  • `flex_emergencia` — o botão "Desligar tudo" vira um job do worker; a
    linha guarda o andamento que a tela consulta.

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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Valores fechados (espelhados nos CHECK da migration 0367).
FLEX_PLATAFORMAS = ("ml", "shopee")
FLEX_DESEJADO = ("ligado", "desligado", "inelegivel")
FLEX_OBSERVADO = ("ligado", "desligado")
FLEX_MODOS = ("desligado", "observar", "piloto", "ativo")
# `pedido_sp` / `pedido_sem_sp`: o robô de prioridade levou o pedido Flex para
# o .sp / o .sp não cobria e ficou o aviso (etapa 2, services/prioridade_estoque).
# `decidir`: a regra mudou o que quer para o anúncio (etapa 3, flex_motor) —
# só a MUDANÇA vira linha, não cada rodada. `acertar_estoque`: uma pessoa
# disse que acertou no Bling o estoque de um pedido Flex que saiu de São
# Bernardo sem passar pelo .sp (routers/flex.py).
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
    "acertar_estoque",
)
FLEX_RESULTADOS = ("ok", "erro", "simulado", "pendente", "ignorado")
# Status do anúncio na plataforma, nos nomes da importação (ListingStatus):
# o da Shopee (NORMAL, UNLIST…) é traduzido para estes (flex_api).
FLEX_STATUS_ANUNCIO = ("active", "paused", "under_review", "inactive", "closed")
FLEX_EMERGENCIA_STATUS = ("na_fila", "rodando", "concluida", "falhou")


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
    # Quando o robô de prioridade levou o pedido para o .sp. O `products.stock`
    # do .sp só cai quando o webhook de estoque do Bling chega (e o da reserva
    # às vezes não chega): até o produto ser atualizado DEPOIS deste instante,
    # o motor continua descontando o pedido do saldo Flex (flex_motor).
    sp_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Pedido Flex que SAIU (em andamento/atendido) sem ter ido ao .sp: o Bling
    # baixou o outro lote, mas a peça saiu de São Bernardo — o .sp do Bling fica
    # com peça a mais. O motor segue descontando até uma pessoa dizer que
    # acertou o estoque no Bling (transferência para o .sp) — este carimbo.
    acertado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acertado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
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
        CheckConstraint(_in("status_anuncio", FLEX_STATUS_ANUNCIO), name="status_anuncio"),
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
    # Fila de leitura justa (revisão de 02/10/2026): a última TENTATIVA de ler
    # o Flex na plataforma, tenha dado certo ou não — a fila anda em rodízio
    # por ela. Leitura que falha (404, 403, token quebrado) não volta ao topo
    # da fila: espera `proxima_leitura`, que cresce com `leitura_falhas`
    # seguidas — antes um anúncio que nunca era lido ficava em 1º lugar em
    # TODA rodada e tomava as vagas de leitura das outras contas.
    leitura_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    leitura_falhas: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    proxima_leitura: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Status do anúncio na plataforma (active | paused | under_review |
    # inactive | closed) e quando foi visto: pela descoberta da conta (ML,
    # busca por status), pela leitura da Shopee (`item_status`) ou pela
    # importação (`listings.status`) — vale o mais novo. Anúncio que não está
    # ativo não ocupa vaga da família e não pede aprovação; com Flex ligado,
    # desliga (ao ser reativado não volta vendendo Flex sem controle).
    status_anuncio: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FlexConta(Base):
    """A conta PODE ter Flex? E a última descoberta dos anúncios dela.

    ML: `GET /flex/sites/MLB/users/{user_id}/subscriptions/v1` — lista de
    assinaturas com `status` "in"/"pending"/"out" (algumas contas respondem
    404/403). Só "in" tem Flex. Shopee: `get_channel_list` da LOJA — o canal
    Entrega Direta (90022) precisa estar `enabled` (e sem `mask_channel_id`)
    na loja; em 02/10/2026 ele existe nas 14 lojas e está desligado em todas.
    Conta que não pode ter Flex não tem leitura nem escrita por anúncio: os
    anúncios dela ficam "não pode ter Flex" com o motivo da conta, sem pedir
    aprovação (antes cada um pedia, e a aprovação não ligava nada)."""

    __tablename__ = "flex_conta"
    __table_args__ = (CheckConstraint(_in("plataforma", FLEX_PLATAFORMAS), name="plataforma"),)

    integration_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("integrations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    plataforma: Mapped[str] = mapped_column(Text, nullable=False)
    # True = pode ter Flex; False = não pode (out/pending/404/403, canal
    # desligado na loja); NULL = ainda não deu para saber.
    flex_ativo: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # O que a plataforma respondeu, cru: "in", "pending", "out", "http_404",
    # "sem_assinatura" (ML); "in", "out", "sem_canal" (Shopee).
    status: Mapped[str | None] = mapped_column(Text, nullable=True)
    detalhe: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Última conferência que trouxe resposta (a "validade" de ~1 h conta
    # daqui) e o erro da última que não trouxe (rede, 5xx) — nesse caso vale
    # a resposta anterior e a próxima rodada pergunta de novo.
    lido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Descoberta (ML): todos os ids da conta (ativos e pausados) — o anúncio
    # que o DaVinci não conhece entra no estado para ser desligado.
    descoberta_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    descoberta_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    descoberta_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    descoberta_novos: Mapped[int | None] = mapped_column(Integer, nullable=True)
    descoberta_erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FlexEmergencia(Base):
    """Um clique no "Desligar tudo (emergência)". O pedido HTTP só tira as
    aprovações (na hora: nada volta a ligar) e põe o job na fila; o worker
    desliga e vai gravando o andamento em `resumo`, que a tela consulta."""

    __tablename__ = "flex_emergencia"
    __table_args__ = (
        CheckConstraint(_in("status", FLEX_EMERGENCIA_STATUS), name="status"),
        CheckConstraint(_in("modo", FLEX_MODOS), name="modo"),
        Index("ix_flex_emergencia_pedido_em", "pedido_em"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    pedido_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Quem pediu (sem FK, como o log: sobrevive ao usuário apagado).
    por: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # Contas da equipe de quem pediu (texto dos ids); NULL = todas as de
    # `flex_contas` (admin ou usuário sem equipe).
    escopo: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    modo: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    # Anúncios que tinham aprovação quando o botão foi apertado ([conta,
    # anúncio]): também são alvo, mesmo lidos desligados — a rodada pode
    # estar no meio de ligá-los.
    aprovados: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    resumo: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    iniciado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terminado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)
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
