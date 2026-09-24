from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

# Origem do chamado — de qual aba/fluxo ele nasceu (coluna "origem" da planilha).
# `vendas` (Eduardo 04/09: "em origens coloque o status vendas") — chamado aberto
# a partir de uma venda, sem passar pela Margem/Logística/Devolução.
ORIGENS = ("margem", "logistica", "devolucao", "vendas")
# Como a réplica sai: `api` = mediação do Mercado Livre via API (claim já
# aberto pelo comprador); `robo` = formulário web/protocolo — fica na fila do
# robô de browser; `manual` = só registra no histórico (operador respondeu
# fora do sistema).
CANAIS = ("api", "robo", "manual")


class Chamado(Base, TimestampMixin):
    """Um chamado aberto na plataforma, no formato da aba `Chamados`:
    Data | pedido bling | pedido marketplace | plataforma | produto | sku |
    conta | status bling | origem | chamado | réplica | réplica automática |
    alterar status bling | observação | valor (lucro/prejuízo).

    O antigo sim/não "monitoramento" saiu (migration 0269): o cron acompanha
    TODO chamado de canal API do ML e fecha sozinho quando o claim encerra.

    Os dados do pedido são espelho do `bling_orders` no momento da criação;
    `status_bling` é o snapshot — a listagem mostra o ATUAL (lookup vivo).
    """

    __tablename__ = "chamados"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    data: Mapped[date | None] = mapped_column(Date, nullable=True)
    pedido_bling: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    pedido_marketplace: Mapped[str | None] = mapped_column(Text, nullable=True)
    plataforma: Mapped[str | None] = mapped_column(Text, nullable=True)
    conta: Mapped[str | None] = mapped_column(Text, nullable=True)
    produto: Mapped[str | None] = mapped_column(Text, nullable=True)
    sku: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_bling: Mapped[str | None] = mapped_column(Text, nullable=True)
    origem: Mapped[str] = mapped_column(Text, nullable=False)
    # id da linha de origem (logistica/refunds/devolutions), quando veio de lá.
    origem_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Nº do chamado: claim_id da mediação (canal api) ou protocolo do
    # formulário (canal robo) — nunca inventado, só o que a plataforma devolveu.
    chamado: Mapped[str | None] = mapped_column(Text, nullable=True)
    chamado_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    canal: Mapped[str] = mapped_column(
        Text, nullable=False, default="manual", server_default="manual"
    )
    # Nome da situação do Bling a aplicar (dropdown de situacao_bling.nome).
    alterar_status_bling: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Réplica automática: reenvia `auto_mensagem` (+ anexos sem mensagem_id) a
    # cada `auto_dias` enquanto ligada e o chamado não estiver resolvido.
    auto_ligada: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    auto_dias: Mapped[int | None] = mapped_column(Integer, nullable=True)
    auto_mensagem: Mapped[str | None] = mapped_column(Text, nullable=True)
    auto_ultimo_envio_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolvido: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false", index=True
    )
    resolvido_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Status OFICIAL que a plataforma deu ao chamado pela API (Vinicius 17/09,
    # migration 0287): código de services.chamados.STATUS_* + desde quando.
    # NULL = a API não disse nada; a listagem deriva do histórico (aguardando
    # plataforma / plataforma respondeu / precisa de humano).
    status_plataforma: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_plataforma_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 22/09 (migration 0308): o número em `chamado` foi capturado pelo robô NA TELA
    # da plataforma (Seller Center / Portal de Atendimento), não veio de API. É um
    # FATO gravado por quem o escreveu (`/agent/resultado` e `/agent/registrar`), e
    # não um palpite: deduzir "é de tela" pela mensagem de abertura erra feio quando
    # a varredura de aberturas presas reencaminha ao robô uma abertura que já tinha
    # um nº de API válido (claim do ML, return_sn da Shopee) — o número continua
    # bom, e o caso continua tendo acompanhamento por API.
    chamado_de_tela: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # 22/09 (Vinicius, 292592, migration 0308): caso ABERTO NA TELA não tem API
    # que o leia — quem lê é o robô, abrindo `chamado_url`. `leitura_robo_at` é a
    # última leitura CONFIRMADA (responde "esse caso está sendo acompanhado?") e
    # `leitura_robo_claim_at` é a entrega em curso (esconde o caso de outro poll e
    # vence sozinha se o robô morrer no meio). São duas perguntas diferentes: juntar
    # num campo só apaga quando o robô de fato leu.
    leitura_robo_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    leitura_robo_claim_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Resultado do chamado em R$ — coluna "Valor" do grupo Controle (Eduardo
    # 03/09, migration 0240). Positivo = lucro ("100 reais ganhamos"), negativo
    # = prejuízo (Eduardo 15/09); obrigatório ao resolver pela aba. NULL = sem valor.
    valor_recuperado: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    # 19/09 (Vinicius, migration 0297): SUGESTÃO de resultado — do robô (ação
    # `resolver` do cérebro) ou da plataforma (compensação paga lida pelo sync).
    # Nada fecha sozinho: a pessoa confirma (ou corrige) ao concluir, e aí vira
    # `valor_recuperado`. Mesmo sinal: positivo = lucro, negativo = prejuízo.
    valor_sugerido: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Encaminhado ao JURÍDICO (Eduardo 04/09, migration 0248): quem/quando, a
    # observação digitada ao encaminhar, o token do dossiê público (link no
    # Threema) e os IDs Threema que receberam. Preenchido = aparece na aba Jurídico.
    juridico_enviado_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    juridico_enviado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    juridico_obs: Mapped[str | None] = mapped_column(Text, nullable=True)
    juridico_token: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    juridico_destinatarios: Mapped[str | None] = mapped_column(Text, nullable=True)

    mensagens: Mapped[list["ChamadoMensagem"]] = relationship(
        back_populates="chamado",
        cascade="all, delete-orphan",
        order_by="ChamadoMensagem.created_at",
    )
    anexos: Mapped[list["ChamadoAnexo"]] = relationship(
        back_populates="chamado",
        cascade="all, delete-orphan",
        order_by="ChamadoAnexo.created_at",
    )


class ChamadoMensagem(Base, TimestampMixin):
    """Histórico do chamado: cada réplica (manual ou automática) enviada, cada
    resposta recebida da plataforma e eventos do sistema — com quem e quando.

    `status`: registrada (canal manual, nada a enviar) | pendente (na fila do
    robô) | enviada | falhou (`erro` explica). 19/09: `falhou` só fica quando o
    robô esgotou as tentativas (`tentativas`, migration 0297) — antes disso a
    mensagem volta pra `pendente` com o `erro` da última tentativa.

    `tipo` `instrucao` (19/09): recado de uma pessoa PRO ROBÔ ("responde que o
    pacote foi entregue dia 12"), `direcao=sistema` — não vai pra plataforma; o
    cérebro lê no `/agent/analisar` e a análise dele consome a instrução.
    """

    __tablename__ = "chamado_mensagem"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    chamado_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("chamados.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # enviada (nós → plataforma) | recebida (plataforma → nós) | sistema
    direcao: Mapped[str] = mapped_column(Text, nullable=False)
    # replica | replica_auto | abertura | resposta | sistema | analise | historico | instrucao
    tipo: Mapped[str] = mapped_column(Text, nullable=False)
    texto: Mapped[str] = mapped_column(Text, nullable=False)
    canal: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    autor_nome: Mapped[str | None] = mapped_column(Text, nullable=True)
    autor_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    enviada_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Quantas vezes o robô já tentou enviar e falhou (ver services.chamados.MAX_TENTATIVAS_ROBO).
    tentativas: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    chamado: Mapped["Chamado"] = relationship(back_populates="mensagens")
    anexos: Mapped[list["ChamadoAnexo"]] = relationship(
        back_populates="mensagem",
        order_by="ChamadoAnexo.created_at",
    )


class ChamadoAnexo(Base, TimestampMixin):
    """Foto anexada a uma réplica (`mensagem_id`) ou à réplica automática do
    chamado (`mensagem_id` NULL). Blob no próprio banco, servido pelo endpoint
    autenticado — mesmo esquema do logistica_status_anexo."""

    __tablename__ = "chamado_anexo"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    chamado_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("chamados.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mensagem_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("chamado_mensagem.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    chamado: Mapped["Chamado"] = relationship(back_populates="anexos")
    mensagem: Mapped["ChamadoMensagem | None"] = relationship(back_populates="anexos")


class ChamadoPedido(Base, TimestampMixin):
    """Pedidos cobertos por um chamado em LOTE — Eduardo 15/09: "não precisa
    ser um chamado para cada pedido, pode juntar: todos ML Aguiar num único
    chamado". O chamado guarda só o 1º pedido em `pedido_bling`; aqui ficam
    todos, com o motivo do atraso de cada um (`fila` = poucos minutos depois
    do corte, `energia` = horas depois, no mesmo dia), pra aba Pedidos do
    Controle de Estoque mostrar o chamado em cada linha e não abrir duas vezes.
    Ver migration 0276 e services/chamados_atraso."""

    __tablename__ = "chamado_pedidos"
    __table_args__ = (
        UniqueConstraint("chamado_id", "pedido_bling", name="uq_chamado_pedidos_chamado_pedido"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    chamado_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("chamados.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    pedido_bling: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    pedido_marketplace: Mapped[str | None] = mapped_column(Text, nullable=True)
    motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    corte_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    postagem_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChamadoCerebro(Base, TimestampMixin):
    """Um cérebro dos chamados com senha própria — quem chama `/agent/analisar`
    e decide em `/agent/analise`.

    Vinicius, 24/09/2026: levar o cérebro do PC do Eduardo pro Hermes no Mac
    Santiago, SUBSTITUINDO o dele. Até aqui o cérebro usava o mesmo token do
    robô de NF e assinava só "cérebro": não dava pra saber quem decidiu, nem
    desligar um sem desligar o outro. E o `/agent/analisar` não reserva nada —
    dois cérebros ao mesmo tempo respondem o mesmo caso duas vezes pra
    plataforma.

    `exclusivo` é a troca de guarda: ligado, o token antigo continua valendo pro
    resto (lease, resultado, recebida — as mãos do Eduardo), mas o
    `/agent/analisar` devolve lista vazia pra ele e o `/agent/analise` recusa.
    O cérebro antigo para sem erro nenhum do lado dele, e desligar o
    `exclusivo` devolve tudo como era.

    O token só aparece uma vez; aqui fica o sha256 (mesmo esquema do
    `ClaudeConector`)."""

    __tablename__ = "chamados_cerebros"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    # Vai no texto da análise ("Análise do robô Hermes [classe] …").
    nome: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    exclusivo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Última vez que o cérebro antigo bateu na porta depois da troca — mostra
    # se o programa do Eduardo ainda está rodando à toa.
    legado_ignorado_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
