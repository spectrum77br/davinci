from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Origem = Literal["margem", "logistica", "devolucao", "vendas"]
Canal = Literal["api", "robo", "manual"]


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class ChamadoAnexoOut(BaseModel):
    id: UUID
    mensagem_id: UUID | None = None
    filename: str
    content_type: str
    size_bytes: int
    created_at: datetime


class ChamadoMensagemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    chamado_id: UUID
    direcao: str
    tipo: str
    texto: str
    canal: str
    status: str
    erro: str | None = None
    autor_nome: str | None = None
    enviada_at: datetime | None = None
    created_at: datetime
    anexos: list[ChamadoAnexoOut] = []


class ChamadoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    data: date | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    plataforma: str | None = None
    conta: str | None = None
    produto: str | None = None
    sku: str | None = None
    # Snapshot gravado na linha; `status_bling_atual` é o lookup VIVO em
    # bling_orders (o que a coluna "status bling" da planilha pede).
    status_bling: str | None = None
    status_bling_atual: str | None = None
    origem: str
    origem_ref: str | None = None
    chamado: str | None = None
    chamado_url: str | None = None
    canal: str
    alterar_status_bling: str | None = None
    auto_ligada: bool = False
    auto_dias: int | None = None
    auto_mensagem: str | None = None
    auto_ultimo_envio_at: datetime | None = None
    auto_proximo_envio_at: datetime | None = None
    resolvido: bool = False
    resolvido_at: datetime | None = None
    observacao: str | None = None
    # Resultado do chamado em R$ — coluna "Valor" do Controle (Eduardo 03/09):
    # positivo = lucro, negativo = prejuízo (15/09); None = ainda sem valor.
    valor_recuperado: Decimal | None = None
    created_at: datetime
    updated_at: datetime
    mensagens_total: int = 0
    ultima_mensagem_at: datetime | None = None
    anexos_auto: list[ChamadoAnexoOut] = []
    # Jurídico (migration 0248)
    juridico_enviado_at: datetime | None = None
    juridico_enviado_por_nome: str | None = None
    juridico_obs: str | None = None
    juridico_link: str | None = None
    juridico_enviados: list[str] = []  # IDs Threema que receberam (CSV no banco)


class JuridicoIn(BaseModel):
    observacao: str | None = None


class JuridicoOut(BaseModel):
    chamado: "ChamadoOut"
    sent: list[str]
    failed: list[str]
    link: str


class ChamadoCreate(BaseModel):
    origem: Origem
    data: date | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    plataforma: str | None = None
    conta: str | None = None
    produto: str | None = None
    sku: str | None = None
    status_bling: str | None = None
    origem_ref: str | None = None
    chamado: str | None = None
    chamado_url: str | None = None
    canal: Canal = "manual"
    alterar_status_bling: str | None = None
    observacao: str | None = None

    _clean = field_validator(
        "pedido_bling",
        "pedido_marketplace",
        "plataforma",
        "conta",
        "produto",
        "sku",
        "status_bling",
        "origem_ref",
        "chamado",
        "chamado_url",
        "alterar_status_bling",
        "observacao",
        mode="before",
    )(_clean_optional_text)

    @model_validator(mode="after")
    def _pedido_obrigatorio(self) -> "ChamadoCreate":
        if not self.pedido_bling and not self.pedido_marketplace:
            raise ValueError("pedido_bling ou pedido_marketplace é obrigatório")
        return self


class ChamadoPatch(BaseModel):
    data: date | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    plataforma: str | None = None
    conta: str | None = None
    produto: str | None = None
    sku: str | None = None
    origem: Origem | None = None
    origem_ref: str | None = None
    chamado: str | None = None
    chamado_url: str | None = None
    canal: Canal | None = None
    alterar_status_bling: str | None = None
    auto_ligada: bool | None = None
    auto_dias: int | None = Field(default=None, ge=1, le=365)
    auto_mensagem: str | None = None
    observacao: str | None = None
    # Resultado do chamado: positivo = lucro, negativo = prejuízo (Eduardo 15/09).
    valor_recuperado: Decimal | None = None

    _clean = field_validator(
        "pedido_bling",
        "pedido_marketplace",
        "plataforma",
        "conta",
        "produto",
        "sku",
        "origem_ref",
        "chamado",
        "chamado_url",
        "alterar_status_bling",
        "auto_mensagem",
        "observacao",
        mode="before",
    )(_clean_optional_text)


class ChamadoPage(BaseModel):
    items: list[ChamadoOut]
    total: int
    limit: int
    offset: int
    plataformas: list[str]
    # Contas pro filtro (Eduardo 15/09: "filtrar por conta, ex. ML Aguiar 2") —
    # restritas à plataforma filtrada, quando há uma.
    contas: list[str] = Field(default_factory=list)


class ChamadoLookupOut(BaseModel):
    data: date | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    plataforma: str | None = None
    conta: str | None = None
    produto: str | None = None
    sku: str | None = None
    status_bling: str | None = None


class SituacoesOut(BaseModel):
    nomes: list[str]


class AlterarStatusIn(BaseModel):
    situacao: str = Field(min_length=1)


class AlterarStatusOut(BaseModel):
    bling_order_id: int
    situacao: str
    situacao_id: int


class ResolverIn(BaseModel):
    resolvido: bool = True
    # Opcional: situação Bling a aplicar junto (ex. Resolvido / Perdimento).
    situacao: str | None = None
    # Resultado do chamado em R$ (positivo = lucro, negativo = prejuízo).
    # OBRIGATÓRIO ao resolver (Eduardo 15/09) — o router devolve 422
    # `chamado_valor_obrigatorio` sem ele; ignorado ao reabrir.
    valor_recuperado: Decimal | None = None

    _clean = field_validator("situacao", mode="before")(_clean_optional_text)


# ------------------------------------------------------------------ robô (agent)
# Contrato do robô de chamados (runner de frete / monitor), autenticado por
# X-Agent-Token — mesmo token do executor de NF.

StatusEnvio = Literal["enviada", "falhou", "pendente", "registrada"]


class AgentRegistrarIn(BaseModel):
    """O robô abriu (ou tentou abrir) um chamado na plataforma: registra a
    linha na aba + a mensagem de abertura no histórico. Idempotente por
    (pedido_bling, origem) enquanto o chamado estiver aberto."""

    pedido_bling: str = Field(min_length=1)
    origem: Origem = "margem"
    plataforma: str | None = "ml"
    conta: str | None = None
    pedido_marketplace: str | None = None
    origem_ref: str | None = None
    chamado: str | None = None
    chamado_url: str | None = None
    mensagem: str | None = None
    status_envio: StatusEnvio = "enviada"
    erro: str | None = None
    observacao: str | None = None

    _clean = field_validator(
        "pedido_bling",
        "plataforma",
        "conta",
        "pedido_marketplace",
        "origem_ref",
        "chamado",
        "chamado_url",
        "mensagem",
        "erro",
        "observacao",
        mode="before",
    )(_clean_optional_text)


class AgentRegistrarOut(BaseModel):
    chamado_id: UUID
    mensagem_id: UUID | None = None
    criado: bool


class AgentTarefaOut(BaseModel):
    """Uma réplica pendente pro robô executar. `abrir` = chamado ainda sem
    protocolo (formulário); `responder` = já tem protocolo (página do caso)."""

    tipo: Literal["abrir", "responder"]
    mensagem_id: UUID
    chamado_id: UUID
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    conta: str | None = None
    plataforma: str | None = None
    chamado: str | None = None
    chamado_url: str | None = None
    texto: str
    anexos: list[UUID] = []


class AgentLeaseIn(BaseModel):
    limite: int = Field(default=10, ge=1, le=100)
    # Só um tipo de tarefa: o robô do formulário pega `abrir` e o do Tuta pega
    # `responder` — sem isso um lease marcava `enviando` as tarefas do outro.
    tipo: Literal["abrir", "responder"] | None = None
    # Plataforma que ESTE robô atende. Vazio = consumidor padrão (o robô do
    # formulário do ML, cujo código não muda): recebe tudo MENOS TikTok/Shopee.
    # "tiktok" / "shopee" = só as tarefas dessa plataforma (abrir no Seller
    # Center); "ml" = só Mercado Livre. Mesmo desenho do lease de NF.
    plataforma: str | None = None


class AgentLeaseOut(BaseModel):
    tarefas: list[AgentTarefaOut]


class AgentResultadoIn(BaseModel):
    mensagem_id: UUID
    ok: bool
    erro: str | None = None
    # Protocolo/URL capturados ao abrir (só vêm na tarefa `abrir`).
    chamado: str | None = None
    chamado_url: str | None = None

    _clean = field_validator("erro", "chamado", "chamado_url", mode="before")(_clean_optional_text)


class AgentRecebidaIn(BaseModel):
    """O monitor leu uma resposta da plataforma: vai pro histórico como
    `recebida`. Identifica o chamado por id OU por (pedido_bling, chamado)."""

    chamado_id: UUID | None = None
    pedido_bling: str | None = None
    chamado: str | None = None
    texto: str = Field(min_length=1)
    resumo: str | None = None
    resolvido: bool = False

    _clean = field_validator("pedido_bling", "chamado", "resumo", mode="before")(
        _clean_optional_text
    )


class AgentRecebidaOut(BaseModel):
    chamado_id: UUID
    mensagem_id: UUID
    resolvido: bool


class AgentAnalisarIn(BaseModel):
    """Chamados de canal robô com resposta da plataforma ainda não analisada
    pelo cérebro (última `recebida` mais nova que a última `analise`)."""

    limite: int = Field(default=20, ge=1, le=100)
    plataforma: str | None = "ml"
    # 09/09: o cérebro passou a olhar TODOS os chamados da aba — `robo` (frete
    # ML, pode responder), `api` (devolução Shopee/TikTok/ML, só decide/avisa)
    # e `manual` (aberto por pessoa: só avisa, nunca age em cima de humano).
    canais: list[Literal["robo", "api", "manual"]] = Field(default=["robo"], min_length=1)


class AgentMensagemOut(BaseModel):
    id: UUID
    direcao: str
    tipo: str
    status: str
    autor_nome: str | None = None
    created_at: datetime
    texto: str


class AgentChamadoAnaliseOut(BaseModel):
    chamado_id: UUID
    chamado: str | None = None
    chamado_url: str | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    conta: str | None = None
    plataforma: str | None = None
    canal: str
    origem: str
    resolvido: bool
    valor_recuperado: Decimal | None = None
    observacao: str | None = None
    created_at: datetime
    mensagens: list[AgentMensagemOut]
    # Prints capturados na abertura (e os sem mensagem) — o cérebro pode
    # reanexá-los na réplica quando o ML pede "os comprovantes" de novo.
    anexos_abertura: list[UUID] = []
    replicas_robo: int = 0
    analises: int = 0


class AgentAnalisarOut(BaseModel):
    chamados: list[AgentChamadoAnaliseOut]


AcaoAnalise = Literal["esperar", "responder", "resolver", "humano"]


class AgentAnaliseIn(BaseModel):
    """Decisão do cérebro sobre a última resposta da plataforma: registra a
    análise no histórico e executa a ação (enfileira réplica pro robô do
    Tuta, resolve com valor recuperado, ou pede humano)."""

    chamado_id: UUID
    classe: str = Field(min_length=1, max_length=60)
    resumo: str = Field(min_length=1, max_length=600)
    acao: AcaoAnalise
    texto_replica: str | None = None
    reanexar_abertura: bool = False
    valor_recuperado: Decimal | None = Field(default=None, ge=0)
    observacao: str | None = None
    # `humano` num chamado que o monitor antigo já tinha fechado: reabre.
    reabrir: bool = False

    _clean = field_validator("texto_replica", "observacao", mode="before")(_clean_optional_text)

    @model_validator(mode="after")
    def _replica_precisa_texto(self) -> "AgentAnaliseIn":
        if self.acao == "responder" and not (self.texto_replica or "").strip():
            raise ValueError("texto_replica é obrigatório quando acao=responder")
        return self


class AgentAnaliseOut(BaseModel):
    chamado_id: UUID
    analise_id: UUID
    replica_id: UUID | None = None
    resolvido: bool


class AgentHistoricoIn(BaseModel):
    """15/09 (Eduardo: "preciso de todo o contexto da conversa"): conversa COMPLETA
    da página do caso (todas as falas desde a abertura na plataforma), guardada à
    parte como contexto pra quem analisa. Não é resposta nova — não passa pelo
    cérebro. Identifica o chamado por id OU por (chamado[, pedido_bling])."""

    chamado_id: UUID | None = None
    pedido_bling: str | None = None
    chamado: str | None = None
    texto: str = Field(min_length=1)

    _clean = field_validator("pedido_bling", "chamado", mode="before")(_clean_optional_text)


class AgentHistoricoOut(BaseModel):
    chamado_id: UUID
    mensagem_id: UUID
    alterado: bool


class AgentPagamentoMlIn(BaseModel):
    """15/09: fatos do pagamento da venda no ML (liberação/estorno/envio) pro
    agente de chamados decidir — e encerrar sozinho o que ficou sem prejuízo."""

    pedidos_bling: list[str] = Field(min_length=1, max_length=50)


class AgentPagamentoMlItem(BaseModel):
    pedido_bling: str
    ok: bool
    erro: str | None = None
    venda: str | None = None
    conta: str | None = None
    pedido_status: str | None = None
    pagamento_status: str | None = None
    pagamento_detalhe: str | None = None
    valor_pago: float | None = None
    pago_em: datetime | None = None
    liberado_em: datetime | None = None
    estorno_valor: float | None = None
    estorno_em: datetime | None = None
    estorno_fonte: str | None = None
    envio_status: str | None = None
    envio_substatus: str | None = None
    sem_prejuizo: bool = False
    resumo: str = ""


class AgentPagamentoMlOut(BaseModel):
    pedidos: list[AgentPagamentoMlItem]

