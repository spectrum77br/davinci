from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class StatusDetalheOut(BaseModel):
    """Uma linha do balãozinho da coluna "Status Plataforma": o campo já
    traduzido + desde quando está assim."""

    campo: str
    rotulo: str
    valor: str
    # ISO-8601 UTC de quando esse campo mudou; None quando ainda não há carimbo
    # (linha antiga, que se carimba sozinha no próximo 🔄/recarregar).
    em: str | None = None
    # plataforma = data oficial do canal | aprox = melhor estimativa do canal |
    # davinci = instante em que o DaVinci viu mudar. Ver logistica_datas.
    fonte: str | None = None


class LogisticaOut(BaseModel):
    id: UUID
    data: date | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    plataforma: str | None = None
    conta: str | None = None
    meli_status: dict[str, str] = Field(default_factory=dict)
    # Assinatura em PT p/ exibir na coluna "Status Plataforma" (derivada de
    # meli_status; vazia quando não há assinatura). O front só renderiza.
    status_plataforma: str = ""
    # A mesma assinatura ABERTA campo a campo, com a data de cada um — alimenta
    # o balãozinho da coluna. Derivada (meli_status × status_datas × plataforma).
    status_detalhe: list[StatusDetalheOut] = Field(default_factory=list)
    rastreio: str | None = None
    localizacao: str | None = None
    # Divergência ML × rastreio físico dos Correios (auto-calculada; só leitura).
    divergencia: str | None = None
    status_bling: str | None = None
    chamado: str | None = None
    observacao: str | None = None
    # Casador da aba Status: regra que casa com a chave (status_plataforma)
    # deste pedido. `acao_match`=achou regra; `acao_status_id`=id da linha da
    # aba Status que casou; `acao_resumo`=o que o sistema faria (só leitura,
    # derivado — o front só renderiza).
    acao_match: bool = False
    acao_status_id: UUID | None = None
    acao_resumo: list[str] = Field(default_factory=list)
    # `acao_monitorar`=alguma regra casada pede monitoramento (fica no painel);
    # `acao_resolvido`=chegou ao fim da cadeia de status (nada mais a fazer). O
    # front oculta a linha quando resolvido E sem monitorar (painel de pendências).
    acao_monitorar: bool = False
    acao_resolvido: bool = False
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class LogisticaCreate(BaseModel):
    data: date | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    plataforma: str | None = None
    conta: str | None = None
    meli_status: dict[str, str] = Field(default_factory=dict)
    rastreio: str | None = None
    localizacao: str | None = None
    status_bling: str | None = None
    chamado: str | None = None
    observacao: str | None = None


class LogisticaPatch(BaseModel):
    data: date | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    plataforma: str | None = None
    conta: str | None = None
    meli_status: dict[str, str] | None = None
    rastreio: str | None = None
    localizacao: str | None = None
    status_bling: str | None = None
    chamado: str | None = None
    observacao: str | None = None


# ---- Aba "Status" (logistica_status) ----


class AnexoOut(BaseModel):
    id: UUID
    filename: str
    content_type: str
    size_bytes: int
    created_at: datetime


class LogisticaStatusOut(BaseModel):
    id: UUID
    plataforma: str | None = None
    status_plataforma: str | None = None
    status_atual: str | None = None
    alterar_status_bling: str | None = None
    monitoramento: bool = False
    abrir_chamado: bool = False
    abrir_reembolso: bool = False
    mensagem_chamado: str | None = None
    mensagem_bling: str | None = None
    mensagem_threema: str | None = None
    threema_recipients: str | None = None
    anexos: list[AnexoOut] = Field(default_factory=list)
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class LogisticaStatusCreate(BaseModel):
    plataforma: str | None = None
    status_plataforma: str | None = None
    status_atual: str | None = None
    alterar_status_bling: str | None = None
    monitoramento: bool = False
    abrir_chamado: bool = False
    abrir_reembolso: bool = False
    mensagem_chamado: str | None = None
    mensagem_bling: str | None = None
    mensagem_threema: str | None = None
    threema_recipients: str | None = None


class LogisticaStatusPatch(BaseModel):
    plataforma: str | None = None
    status_plataforma: str | None = None
    status_atual: str | None = None
    alterar_status_bling: str | None = None
    monitoramento: bool | None = None
    abrir_chamado: bool | None = None
    abrir_reembolso: bool | None = None
    mensagem_chamado: str | None = None
    mensagem_bling: str | None = None
    mensagem_threema: str | None = None
    threema_recipients: str | None = None


class ThreemaDestinatarioOut(BaseModel):
    """Um destinatário do Threema pro seletor do front (`id` + `nome`)."""

    id: str
    nome: str


class EnviarThreemaIn(BaseModel):
    """Corpo do envio: destinatários escolhidos (None/vazio = usa a lista fixa
    do `.env`) + pedido/loja opcionais pra compor o cabeçalho da mensagem."""

    recipients: list[str] | None = None
    pedido: str | None = None
    loja: str | None = None


class EnviarThreemaOut(BaseModel):
    """Resultado do envio da mensagem_threema de uma linha da aba Status."""

    sent: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)


class MensagemBlingPreviewOut(BaseModel):
    """Dry-run da Mensagem Bling: o que SERIA escrito nas Observações do pedido
    (nenhuma escrita foi feita). `put_body` é o corpo exato do PUT."""

    bling_order_id: int
    mensagem: str
    observacoes_atual: str | None = None
    observacoes_novo: str
    put_body: dict


class MensagemBlingOut(BaseModel):
    """Resultado de aplicar a Mensagem Bling nas Observações do pedido."""

    bling_order_id: int
    observacoes_novo: str


class StatusBlingPreviewOut(BaseModel):
    """Dry-run da mudança de situação: mostra a transição da regra
    (`situacao_de` → `situacao_alvo`, nome + id), a situação ATUAL do pedido no
    Bling e se a mudança se aplica. `ja_no_alvo`=pedido já está na situação
    alvo; `aplicavel`=pedido está no `situacao_de` (ou a regra não exige um "de"
    específico), então a mudança pode seguir sem regredir."""

    bling_order_id: int
    situacao_de: str | None = None
    situacao_de_id: int | None = None
    situacao_alvo: str
    situacao_alvo_id: int
    situacao_atual_id: int | None = None
    situacao_atual_nome: str | None = None
    ja_no_alvo: bool = False
    aplicavel: bool = True


class StatusBlingOut(BaseModel):
    """Resultado de mudar a situação do pedido no Bling."""

    bling_order_id: int
    situacao_alvo: str
    situacao_alvo_id: int


class RecarregarOut(BaseModel):
    """Confirmação de que a recarga em massa (enriquecer ML + aplicar status no
    Bling) foi enfileirada em background. `job_id` deixa o front acompanhar o
    andamento em GET /recarregar/{job_id} e avisar quando terminar."""

    enqueued: bool = True
    job_id: str | None = None


class RecarregarStatusOut(BaseModel):
    """Andamento da recarga em massa: `status` é o estado do job no arq
    (queued/deferred/in_progress/complete/failed/not_found). Quando complete,
    `resumo` traz os contadores do serviço (status_refresh, cleanup,
    enrich_updated, status_aplicados…) pro toast do front."""

    status: str
    resumo: dict[str, int] | None = None


# ---- Sugestão de Status Bling (a partir dos status do Meli) ----


class SugestaoIn(BaseModel):
    """Seleção parcial dos 8 campos de status do Meli."""

    meli_status: dict[str, str] = Field(default_factory=dict)


class CandidatoOut(BaseModel):
    status_bling: str
    matches: int


class SugestaoOut(BaseModel):
    candidatos: list[CandidatoOut]


class OpcoesOut(BaseModel):
    """Campos + opções distintas (pra popular os selects do formulário)."""

    field_order: list[str]
    field_labels: dict[str, str]
    field_options: dict[str, list[str]]
    # Nomes das situações do Bling (davinci.situacao_bling), pra o dropdown de
    # "Alterar Status Bling" na aba Status.
    status_bling_options: list[str] = Field(default_factory=list)
