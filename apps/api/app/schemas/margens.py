from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

MargensStatus = Literal["Pendente", "Reprovado", "Aprovado"]
ALLOWED_STATUS: tuple[MargensStatus, ...] = ("Pendente", "Reprovado", "Aprovado")


class MargensOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    data: datetime | None = None
    pedido_bling: int | None = None
    pedido_plataforma: str | None = None
    plataforma: str | None = None
    conta: str | None = None
    sku: str | None = None
    produtos: str | None = None
    custo: float | None = None
    lucro: float | None = None
    margem: float | None = None
    margem_min: float | None = None
    status: MargensStatus = "Pendente"
    observacao: str | None = None

    @field_validator("status", mode="before")
    @classmethod
    def _default_status(cls, v: str | None) -> str:
        if v is None or v == "":
            return "Pendente"
        if v not in ALLOWED_STATUS:
            return "Pendente"
        return v


class MargensPatch(BaseModel):
    status: MargensStatus | None = None
    observacao: str | None = None
    local_only: bool = False


class MargensMarketplacePage(BaseModel):
    items: list["MargensMarketplaceOut"]
    total: int
    limit: int
    offset: int
    platforms: list[str]
    contas: list[str]
    # Lookup-only: True when snapshot didn't have the pedido but bling_orders
    # does — frontend offers a "buscar no historico" CTA that re-requests
    # with force_refresh=true (slow path, ~3min).
    historico_disponivel: bool = False


class MargensMarketplaceOut(BaseModel):
    """Per-item row from vw_conciliacao_margens_marketplace (last 30 days)."""

    model_config = ConfigDict(from_attributes=True)

    bling_order_item_id: UUID
    bling_id: int | None = None
    data: datetime | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    plataforma: str | None = None
    conta: str | None = None
    sku: str | None = None
    produto: str | None = None
    quantidade: int | None = None

    custo_produto: float | None = None
    frete_plataforma: float | None = None
    frete_anuncio: float | None = None
    frete_projetado: float | None = None
    reembolso: float | None = None
    resultado_frete: float | None = None
    saldo_plataforma: float | None = None
    saldo_bling: float | None = None
    saldo_efetivo: float | None = None

    margem: float | None = None
    margem_bling: float | None = None
    margem_minima: float | None = None
    situacao_id: int | None = None
    situacao: str | None = None
    ajustes: float | None = None
    saldo_final: float | None = None
    status: str | None = None

    pricing_account_id: UUID | None = None
    pricing_account_name: str | None = None
    pricing_account_listing_type: str | None = None
    pricing_leaf_segment_name: str | None = None
    bling_listing_type: str | None = None
    observacao: str | None = None

    attention_margem: bool = False
    attention_frete: bool = False
    attention_saldo: bool = False


MargensMarketplacePage.model_rebuild()
