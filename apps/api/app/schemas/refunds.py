from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RefundTipo = Literal["Logistica", "Cliente", "Manutenção", "Extraviado"]


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _clamp_cliente_reembolso(tipo: str | None, reembolso: float | None) -> float | None:
    """Mirror frontend behavior: when tipo='Cliente', reembolso must be <= 0.
    Positive values are auto-negated. Negative/null/zero pass through."""
    if tipo == "Cliente" and reembolso is not None and reembolso > 0:
        return -reembolso
    return reembolso


class RefundOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    data: datetime | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    plataforma: str | None = None
    conta: str
    tipo: RefundTipo | None = None
    prejuizo: float | None = None
    reembolso: float | None = None
    chamado: str | None = None
    chamado_url: str | None = None
    chamado_resolvido: bool = False
    operacao: str | None = None
    conferido: bool
    observacao: str | None = None
    created_at: datetime
    updated_at: datetime


class RefundCreate(BaseModel):
    data: datetime | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    plataforma: str | None = None
    conta: str = Field(min_length=1)
    tipo: RefundTipo | None = None
    prejuizo: float | None = None
    reembolso: float | None = None
    chamado: str | None = None
    chamado_url: str | None = None
    chamado_resolvido: bool = False
    operacao: str | None = None
    observacao: str | None = None

    @field_validator(
        "pedido_bling",
        "pedido_marketplace",
        "plataforma",
        "tipo",
        "chamado",
        "chamado_url",
        "operacao",
        "observacao",
        mode="before",
    )
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        return _clean_optional_text(value)

    @field_validator("conta")
    @classmethod
    def clean_conta(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("conta cannot be blank")
        return value

    @model_validator(mode="after")
    def _enforce_cliente_reembolso(self) -> "RefundCreate":
        self.reembolso = _clamp_cliente_reembolso(self.tipo, self.reembolso)
        return self


class RefundPatch(BaseModel):
    data: datetime | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    plataforma: str | None = None
    conta: str | None = None
    tipo: RefundTipo | None = None
    prejuizo: float | None = None
    reembolso: float | None = None
    chamado: str | None = None
    chamado_url: str | None = None
    chamado_resolvido: bool | None = None
    operacao: str | None = None
    conferido: bool | None = None
    observacao: str | None = None

    @field_validator(
        "pedido_bling",
        "pedido_marketplace",
        "plataforma",
        "conta",
        "tipo",
        "chamado",
        "chamado_url",
        "operacao",
        "observacao",
        mode="before",
    )
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        return _clean_optional_text(value)


class RefundPage(BaseModel):
    items: list[RefundOut]
    total: int
    limit: int
    offset: int
    platforms: list[str]


class RefundLookupOut(BaseModel):
    data: datetime | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    plataforma: str | None = None
    conta: str
    custo_produto: float | None = None
    custo_manutencao: float | None = None


class RefundLookupPage(BaseModel):
    items: list[RefundLookupOut]
    # Lookup-only: True when the recent conciliation view missed the order but
    # bling_orders has it, so the frontend can offer the slow history search.
    historico_disponivel: bool = False


class RefundOrderCostOut(BaseModel):
    pedido_bling: str
    conta: str
    custo_produto: float | None = None
