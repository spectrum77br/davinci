from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


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
