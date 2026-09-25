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
    # 25/09 (294571): ID da consulta no Portal de Atendimento ao Vendedor da Shopee
    # (seller-service.cs.shopee.com.br/detail/<ID>) quando a pessoa abriu uma À MÃO
    # além da devolução — o executor de leitura lê as duas. Mig 0327.
    consulta_portal: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    # 25/09 (Vinicius, lixeirinha no histórico): "excluir" ESCONDE — some da tela e da
    # leitura da IA de Chamado, mas a linha fica. A varredura só grava fala que ainda
    # não está no histórico (apagada, voltaria na passada seguinte) e marcas de sistema
    # seguram respostas automáticas (sem elas o robô repetiria a recusa). Status da aba
    # e robôs continuam contando a mensagem escondida (migration 0329).
    excluida_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    excluida_por: Mapped[str | None] = mapped_column(Text, nullable=True)

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
    # 24/09 (aba "IA de Chamado"): desligada = não recebe nada e não decide; ligada
    # = decide de verdade (Vinicius: "ligado e desligado apenas… vamos pôr ele de
    # verdade", sem modo teste). Quem liga é a pessoa, pela aba.
    ligada: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )


class ChamadoLeitor(Base, TimestampMixin):
    """Um robô que LÊ o caso na tela e devolve o que viu — com senha própria.

    Vinicius, 24/09/2026 (296012): a recusa escrita da Shopee ("Histórico da
    Solicitação") só existe no Seller Center; a API diz só "aguardando análise",
    e às 16:09 ainda dizia isso de uma recusa das 15:59. Quem lê é o
    "executor de leitura de chamado" no Mac Santiago (AdsPower + Seller Center).

    Senha própria, e não o `NF_AGENT_TOKEN`: esse é das mãos do Eduardo (lease,
    resultado, recebida) — com ele o leitor poderia postar na conversa com o
    cliente. Aqui só existem as rotas `/agent/leitor/*`, que não escrevem nada
    pra plataforma. O token só aparece uma vez; aqui fica o sha256 (mesmo
    esquema do `ChamadoCerebro`)."""

    __tablename__ = "chamados_leitores"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    nome: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChamadoIaRegra(Base, TimestampMixin):
    """Uma regra do manual da IA de Chamado — "quando acontecer isso, faça isso".

    Vinicius, 24/09/2026: "essa aba que vai criar é onde eu vou ensinar ele, e onde
    vai criando um manual — quando acontecer isso e isso você faz isso". A IA lê
    o manual inteiro a cada passada e interpreta o texto (não é fórmula). O
    manual mora aqui, não na máquina da IA: trocar a IA de máquina não perde nada.
    `plataforma` NULL = vale pra todas."""

    __tablename__ = "chamados_ia_regras"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    quando: Mapped[str] = mapped_column(Text, nullable=False)
    faca: Mapped[str] = mapped_column(Text, nullable=False)
    plataforma: Mapped[str | None] = mapped_column(Text, nullable=True)
    ativa: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class ChamadoIaAvaliacao(Base, TimestampMixin):
    """✓ acertou / ✗ errou numa decisão da IA de Chamado (uma por análise).

    Vinicius, 24/09/2026 (aba IA de Chamado): "como eu faço pra dizer: nesse você
    errou, nesse você acertou". O ✗ leva a correção ("o que era o certo") — ela
    vira instrução no chamado (a IA refaz na próxima passada) e aprendizado: a IA
    recebe as correções e as confirmações a cada passada, junto com o manual."""

    __tablename__ = "chamados_ia_avaliacoes"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    mensagem_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("chamado_mensagem.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    chamado_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("chamados.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    certo: Mapped[bool] = mapped_column(Boolean, nullable=False)
    correcao: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
