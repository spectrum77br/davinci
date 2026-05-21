from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

RefundTipo = Literal["Logistica", "Cliente", "Manutenção", "Extraviado"]


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


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
    operacao: str | None = None
    observacao: str | None = None

    @field_validator(
        "pedido_bling",
        "pedido_marketplace",
        "plataforma",
        "tipo",
        "chamado",
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


class RefundOrderCostOut(BaseModel):
    pedido_bling: str
    conta: str
    custo_produto: float | None = None
