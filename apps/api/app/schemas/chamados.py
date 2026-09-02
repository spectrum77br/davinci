from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Origem = Literal["margem", "logistica", "devolucao"]
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
    monitoramento: bool = False
    auto_ligada: bool = False
    auto_dias: int | None = None
    auto_mensagem: str | None = None
    auto_ultimo_envio_at: datetime | None = None
    auto_proximo_envio_at: datetime | None = None
    resolvido: bool = False
    resolvido_at: datetime | None = None
    observacao: str | None = None
    created_at: datetime
    updated_at: datetime
    mensagens_total: int = 0
    ultima_mensagem_at: datetime | None = None
    anexos_auto: list[ChamadoAnexoOut] = []


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
    monitoramento: bool = False
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
    monitoramento: bool | None = None
    auto_ligada: bool | None = None
    auto_dias: int | None = Field(default=None, ge=1, le=365)
    auto_mensagem: str | None = None
    observacao: str | None = None

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

    _clean = field_validator("situacao", mode="before")(_clean_optional_text)
