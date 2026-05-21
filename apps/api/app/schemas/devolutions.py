from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class DevolutionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    data: datetime | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    conta: str
    sku: str | None = None
    produtos: str | None = None
    custo_produto: float | None = None
    condicao_produto: str | None = None
    link_abertura: str | None = None
    reembolso: bool
    motivo_devolucao: str | None = None
    custo_manutencao: float | None = None
    tecnico: str | None = None
    devolver_estoque: str | None = None
    observacao: str | None = None
    created_at: datetime
    updated_at: datetime


class DevolutionCreate(BaseModel):
    data: datetime | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    conta: str = Field(min_length=1)
    sku: str | None = None
    produtos: str | None = None
    custo_produto: float | None = None
    condicao_produto: str | None = None
    link_abertura: str | None = None
    reembolso: bool = False
    motivo_devolucao: str | None = None
    custo_manutencao: float | None = None
    tecnico: str | None = None
    devolver_estoque: str | None = None
    observacao: str | None = None

    @field_validator(
        "pedido_bling",
        "pedido_marketplace",
        "sku",
        "produtos",
        "condicao_produto",
        "link_abertura",
        "motivo_devolucao",
        "tecnico",
        "devolver_estoque",
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


class DevolutionPatch(BaseModel):
    data: datetime | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    conta: str | None = None
    sku: str | None = None
    produtos: str | None = None
    custo_produto: float | None = None
    condicao_produto: str | None = None
    link_abertura: str | None = None
    reembolso: bool | None = None
    motivo_devolucao: str | None = None
    custo_manutencao: float | None = None
    tecnico: str | None = None
    devolver_estoque: str | None = None
    observacao: str | None = None

    @field_validator(
        "pedido_bling",
        "pedido_marketplace",
        "conta",
        "sku",
        "produtos",
        "condicao_produto",
        "link_abertura",
        "motivo_devolucao",
        "tecnico",
        "devolver_estoque",
        "observacao",
        mode="before",
    )
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        return _clean_optional_text(value)


class DevolutionPage(BaseModel):
    items: list[DevolutionOut]
    total: int
    limit: int
    offset: int


class DevolutionLookupOut(BaseModel):
    data: datetime | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    conta: str
    sku: str | None = None
    produtos: str | None = None
    custo_produto: float | None = None
