from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Logistica(Base, TimestampMixin):
    """Caso de logística/pós-venda a acompanhar. Registro manual, no formato da
    aba `logistica`: Data | pedido bling | pedido marketplace | plataforma |
    conta | STATUS PLATAFORMA | rastreio | localização | STATUS BLING | Chamado.

    `meli_status` guarda a assinatura de status do Meli (os 8 campos:
    order_status, ship_status, ship_substatus, cancel_group, return_status,
    claim_stage, claim_status, benefited) que alimenta a sugestão de Status
    Bling candidato (app.services.logistica_rules.sugerir). A classificação
    final (`status_bling`) é decisão do operador — a sugestão nunca grava
    sozinha.
    """

    __tablename__ = "logistica"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    data: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    pedido_bling: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    pedido_marketplace: Mapped[str | None] = mapped_column(Text, nullable=True)
    plataforma: Mapped[str | None] = mapped_column(Text, nullable=True)
    conta: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Assinatura de status do Meli (dict dos 8 campos). Vazio pra plataformas
    # sem esse fluxo — a sugestão simplesmente não se aplica.
    meli_status: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    # "Isso é de quando?" — carimbo por campo do `meli_status`:
    # {"ship_substatus": {"em": "2026-08-12T08:05:00+00:00", "fonte": "aprox"}}.
    # Fica FORA do meli_status de propósito: aquele dict é a assinatura que casa
    # com as regras da aba Status (`_clean_meli` descarta qualquer chave extra).
    # Ver app.services.logistica_datas.
    status_datas: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    # Última transição de situação que o ROBÔ aplicou pela aba Status:
    # {"alvo_id", "de_id", "assinatura", "em"}. Se depois disso um humano tirar
    # o pedido do alvo sem a assinatura da plataforma mudar, o robô NÃO reaplica
    # (Eduardo 09/09: "mudou pra Entregue 3x e o sistema volta pra aguardando").
    auto_status: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Número de rastreio do envio (manual por enquanto).
    rastreio: Mapped[str | None] = mapped_column(Text, nullable=True)
    localizacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Memória do 17track (ver services/logistica_track_sync): QUAL número já foi
    # registrado lá. Diferente de `rastreio` = código novo/nunca registrado, e o
    # job registra (sem isso gastaria quota registrando o mesmo número toda hora).
    rastreio_17track: Mapped[str | None] = mapped_column(Text, nullable=True)
    rastreio_17track_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Quando a `localizacao` veio dos Correios pela última vez (push ou pull do
    # 17track). Vazio = a localização exibida ainda é o proxy do marketplace.
    localizacao_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Quando PERGUNTAMOS o rastreio (mesmo sem evento novo). Diferente de
    # `localizacao_at`, que só muda quando o pacote se move — era ele que a
    # tela mostrava como "lido há X h", fazendo parecer que o sistema tinha
    # parado quando o pacote é que estava parado (Eduardo, 10/09).
    rastreio_lido_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Explicação da divergência entre o status do ML e o rastreio físico dos
    # Correios (auto-calculada; vazia quando batem). Ver logistica_rules.
    divergencia: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Classificação escolhida pelo operador (dica vem de logistica_rules.sugerir).
    status_bling: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Número/ref do chamado aberto na plataforma (manual).
    chamado: Mapped[str | None] = mapped_column(Text, nullable=True)
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Instante em que o aviso Threema desta linha foi enviado. Enquanto NULL, a
    # regra casada com `mensagem_threema` mantém o pedido no painel; depois de
    # enviado a mensagem deixa de contar como pendência (o pedido resolve/some).
    threema_enviado_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Abertura AUTOMÁTICA do chamado pelo motor do recarregar (regra da aba
    # Status com "Abrir chamado"): quando tentou pela última vez e, se recusou,
    # o código do motivo (ex. logistica_sem_reclamacao). Sucesso = `chamado`
    # preenchido + erro NULL. Evita bater na API do ML a cada 5 min.
    chamado_auto_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    chamado_auto_erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ---- Amazon (projeto de 15/09/2026, ver services/logistica_amazon_canal) ----
    # Canal de envio persistido: 'dba' (Delivery by Amazon — EasyShip presente
    # ou serviço "Logistica Amazon Dba" no Bling), 'proprio' (MFN sem EasyShip —
    # o vendedor posta nos Correios) ou 'fba' (AFN). NULL = ainda sem sinal.
    amazon_canal: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    # Objeto de postagem do Bling: serviço ("SEDEX", "Logistica Amazon Dba"),
    # data de saída e previsão de entrega (dataSaida + prazoEntregaPrevisto em
    # dias úteis — é a "Data de entrega" que o Bling mostra na cotação).
    servico_envio: Mapped[str | None] = mapped_column(Text, nullable=True)
    postagem_data: Mapped[date | None] = mapped_column(Date, nullable=True)
    previsao_correios: Mapped[date | None] = mapped_column(Date, nullable=True)
    # LatestDeliveryDate da SP-API = "Prazo para entrega" do Seller Central
    # (data em Brasília). Depois dela a Amazon reembolsa o cliente.
    prazo_entrega_amazon: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Entrega confirmada: 17track "Delivered" (Correios) ou EasyShip "Delivered".
    entregue_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Ocorrência grave dos Correios (apreendido, extraviado, devolvido…): o
    # texto do evento e quando o DaVinci viu — dispara a mensagem ao cliente.
    problema_correios: Mapped[str | None] = mapped_column(Text, nullable=True)
    problema_correios_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Contato do pedido no Bling: nome e o e-mail de retransmissão da Amazon
    # (…@marketplace.amazon.com.br). Só esse endereço recebe mensagem — e-mail
    # real de cliente NUNCA é guardado aqui (política da Amazon).
    cliente_nome: Mapped[str | None] = mapped_column(Text, nullable=True)
    cliente_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Carimbos dos avisos Threema do robô (services/logistica_amazon_avisos):
    # cada um sai UMA vez por pedido.
    aviso_previsao_correios_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    aviso_prazo_amazon_3d_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    aviso_prazo_amazon_vencido_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Última leitura do pedido/objeto de postagem no Bling (throttle do enrich).
    bling_enriquecido_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    mensagens_cliente: Mapped[list["LogisticaMensagemCliente"]] = relationship(
        back_populates="logistica",
        cascade="all, delete-orphan",
        order_by="LogisticaMensagemCliente.created_at",
    )


class LogisticaMensagemTemplate(Base, TimestampMixin):
    """Texto editável (tela Logística › Mensagens ao cliente) de cada evento
    que o DaVinci manda ao comprador da Amazon: `problema_correios`,
    `previsao_vencida`, `entregue`. Sem linha = texto padrão do código
    (services/logistica_cliente_mensagens.TEMPLATES_PADRAO). `ativo=false`
    desliga o evento sem apagar o texto."""

    __tablename__ = "logistica_mensagem_template"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    evento: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    assunto: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    corpo: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    ativo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )


class LogisticaMensagemCliente(Base, TimestampMixin):
    """Histórico das mensagens mandadas ao comprador (e-mail pro endereço de
    retransmissão da Amazon). UMA por pedido × evento — é o que garante que o
    cliente não recebe o mesmo aviso duas vezes. `enviado_em` vazio + `erro`
    = falhou (o robô retenta até `tentativas` estourar)."""

    __tablename__ = "logistica_mensagem_cliente"
    __table_args__ = (UniqueConstraint("logistica_id", "evento"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    logistica_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("logistica.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    evento: Mapped[str] = mapped_column(Text, nullable=False)
    destinatario: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    assunto: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    corpo: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    enviado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    tentativas: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    logistica: Mapped["Logistica"] = relationship(back_populates="mensagens_cliente")


class LogisticaStatus(Base, TimestampMixin):
    """Aba "Status": cadastro/referência do que fazer pra cada STATUS PLATAFORMA.

    Formato da aba `status pla.`: STATUS PLATAFORMA | alterar status bling |
    abrir chamado | mensagem chamado. É cadastro manual — o operador preenche as
    células à mão (todos os campos são opcionais). A `mensagem_chamado` também
    guarda o que anexar (foto/link/o que for) do envio.
    """

    __tablename__ = "logistica_status"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    # Marketplace a que a regra se aplica (ex. "Mercado Livre"); vazio = geral.
    plataforma: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Opcional: linha pode nascer vazia pra ser completada pelo operador.
    status_plataforma: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Status que se identifica hoje no Bling (referência, antes de alterar).
    status_atual: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Novo status do Bling; se vazio "não faz nada" (obs "alterado logística").
    alterar_status_bling: Mapped[str | None] = mapped_column(Text, nullable=True)
    monitoramento: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    abrir_chamado: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    abrir_reembolso: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Mensagem do chamado + o que anexar (foto/link/o que for) no envio.
    mensagem_chamado: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Mensagem a colar no Bling.
    mensagem_bling: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Mensagem a enviar às pessoas (Threema) notificando o problema.
    mensagem_threema: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Threema IDs escolhidos pra ESTA regra (separados por vírgula). Salvos pelo
    # seletor 👤 da aba Status; o envio usa essa lista por padrão. Vazio = lista
    # fixa do `.env`.
    threema_recipients: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    anexos: Mapped[list["LogisticaStatusAnexo"]] = relationship(
        back_populates="status",
        cascade="all, delete-orphan",
        order_by="LogisticaStatusAnexo.created_at",
    )


class LogisticaStatusAnexo(Base, TimestampMixin):
    """Imagem anexada à `mensagem_chamado` de uma linha da aba Status.

    Guarda o arquivo como blob (bytea) no próprio banco — o app não tem storage
    externo. Servido de volta pelo endpoint autenticado (cookie) pra o <img>.
    """

    __tablename__ = "logistica_status_anexo"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    status_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("logistica_status.id", ondelete="CASCADE"),
        nullable=False,
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

    status: Mapped["LogisticaStatus"] = relationship(back_populates="anexos")
