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
    # True = saldo_plataforma é projeção (líquido real ainda não liquidado).
    # Frontend: mostra "≈" e NÃO trata como divergência (não zera o Efetivo).
    saldo_projetado: bool = False
    saldo_bling: float | None = None
    # Valor digitado NA MÃO (03/09) — preenche o Efetivo de ML/Shopee/TikTok
    # enquanto o líquido real não sincroniza; o real vence quando chega.
    saldo_manual: float | None = None
    saldo_efetivo: float | None = None

    margem: float | None = None
    margem_bling: float | None = None
    margem_pos_reembolso: float | None = None
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
    # True = Data Especial do segmento ativa para este pedido (período casa e a
    # margem passa na regra especial) — o gatilho de margem baixa fica
    # suspenso. Frontend: badge "data especial" na coluna Margem Mín.
    data_especial: bool = False


class SaldoRaioXItem(BaseModel):
    """Uma linha-item do pedido no raio-X do saldo (pack rateia por proporção)."""

    sku: str | None = None
    produto: str | None = None
    quantidade: int | None = None
    proporcao: float | None = None
    bling_valorbase: float | None = None
    bling_custofrete: float | None = None
    bling_taxacomissao: float | None = None
    saldo_bling: float | None = None
    saldo_plataforma: float | None = None


class SaldoRaioXOut(BaseModel):
    """Raio-X do saldo de um pedido: os componentes que formam o Saldo Bling e
    o Saldo Plataforma exibidos na aba Margem (mesma fonte da listagem — o
    snapshot —, então os números batem com a tela)."""

    pedido_bling: str
    pedido_marketplace: str | None = None
    plataforma: str | None = None
    conta: str | None = None
    data: datetime | None = None
    situacao: str | None = None

    # Lado Bling: o pedido de venda como está gravado no Bling.
    bling_valorbase: float | None = None
    bling_custofrete: float | None = None
    bling_taxacomissao: float | None = None
    saldo_bling: float | None = None

    # Lado plataforma: o repasse como o marketplace informou.
    mp_valor_bruto: float | None = None
    mp_taxas: float | None = None
    mp_frete: float | None = None
    mp_rebate: float | None = None
    mp_desconto: float | None = None
    mp_reembolso: float | None = None
    mp_imposto: float | None = None
    mp_ajuste: float | None = None
    mp_liquido: float | None = None
    mp_atualizado_em: datetime | None = None
    saldo_plataforma: float | None = None

    # Pré-liquidação (Amazon/TikTok/Shopee/ML): o líquido real ainda não chegou
    # e o número da tela é uma projeção (valor base − frete projetado − comissão
    # da conta). Nome mantido por compatibilidade; não é mais só Amazon.
    projecao_amazon: bool = False
    proj_frete_projetado: float | None = None
    proj_comissao_frac: float | None = None

    reembolso_ajustes: float | None = None
    diferenca: float | None = None
    itens: list[SaldoRaioXItem] = []


MargensMarketplacePage.model_rebuild()
