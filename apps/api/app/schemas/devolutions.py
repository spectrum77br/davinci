from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class BlingStockResultOut(BaseModel):
    ok: bool
    action: str
    sku: str | None = None
    bling_product_id: int | None = None
    message: str = ""


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
    devolver_estoque: bool = False
    manutencao: bool = False
    observacao: str | None = None
    troca_sku: str | None = None
    troca_condicao: str | None = None
    estoque_suffix: str | None = None
    quantidade: int = 1
    estoque_destino_sku: str | None = None
    estoque_nova_tag: str | None = None
    manutencao_destino: str | None = None
    tag: str | None = None
    data_devolvido_estoque: datetime | None = None
    prazo: datetime | None = None
    estoque_mov_sku: str | None = None
    estoque_mov_bling_id: int | None = None
    estoque_mov_action: str | None = None
    estoque_mov_qty: int | None = None
    estoque_mov_revertido_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    bling_stock_result: BlingStockResultOut | None = None


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
    devolver_estoque: bool = False
    observacao: str | None = None
    troca_sku: str | None = None
    troca_condicao: str | None = None
    estoque_suffix: str | None = None
    quantidade: int = 1
    estoque_destino_sku: str | None = None
    estoque_nova_tag: str | None = None
    manutencao_destino: str | None = None

    @field_validator(
        "pedido_bling",
        "pedido_marketplace",
        "sku",
        "produtos",
        "condicao_produto",
        "link_abertura",
        "motivo_devolucao",
        "tecnico",
        "observacao",
        "troca_sku",
        "troca_condicao",
        "estoque_suffix",
        "estoque_destino_sku",
        "estoque_nova_tag",
        "manutencao_destino",
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
    def validate_link_required(self) -> "DevolutionCreate":
        if self.condicao_produto in ("Extraviado", "Manutenção") and not self.link_abertura:
            raise ValueError("link_abertura obrigatório para Extraviado / Manutenção")
        return self


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
    devolver_estoque: bool | None = None
    observacao: str | None = None
    troca_sku: str | None = None
    troca_condicao: str | None = None
    estoque_suffix: str | None = None
    quantidade: int | None = None
    estoque_destino_sku: str | None = None
    estoque_nova_tag: str | None = None
    manutencao_destino: str | None = None

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
        "observacao",
        "troca_sku",
        "troca_condicao",
        "estoque_suffix",
        "estoque_destino_sku",
        "estoque_nova_tag",
        "manutencao_destino",
        mode="before",
    )
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        return _clean_optional_text(value)


class StockCorrectionIn(BaseModel):
    """Correção manual de estoque: adiciona unidades de um SKU ao estoque Bling
    com a MESMA lógica de devolução (Novo/Usado → bin existente ou z000N.<tag>),
    sem criar registro de devolução nem alterar situação de pedido."""

    sku: str = Field(min_length=1)
    condicao_produto: str = Field(min_length=1)
    quantidade: int = Field(default=1, ge=1)
    produtos: str | None = None
    custo_produto: float | None = None
    observacao: str | None = None
    troca_sku: str | None = None
    troca_condicao: str | None = None
    estoque_suffix: str | None = None
    estoque_destino_sku: str | None = None
    estoque_nova_tag: str | None = None
    manutencao_destino: str | None = None

    @field_validator(
        "produtos",
        "observacao",
        "troca_sku",
        "troca_condicao",
        "estoque_suffix",
        "estoque_destino_sku",
        "estoque_nova_tag",
        "manutencao_destino",
        mode="before",
    )
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        return _clean_optional_text(value)

    @field_validator("sku", "condicao_produto")
    @classmethod
    def clean_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("cannot be blank")
        return value


class DevolutionPage(BaseModel):
    items: list[DevolutionOut]
    total: int
    limit: int
    offset: int


class DevolutionProductOut(BaseModel):
    """Item da busca de produtos do modal de troca (Modal 1)."""
    sku: str
    name: str
    cost_price: float | None = None
    saldo_virtual_total: int | None = None


class SkuSuffixVariant(BaseModel):
    suffix: str
    sku: str
    name: str | None = None
    exists: bool


class SkuSuffixesOut(BaseModel):
    """Variantes de sufixo de um SKU para o modal `.sp` (Modal 2)."""
    base: str
    allowed_suffixes: list[str]
    variants: list[SkuSuffixVariant]


class DevolutionLookupOut(BaseModel):
    data: datetime | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    conta: str
    sku: str | None = None
    produtos: str | None = None
    quantidade: int | None = None
    custo_produto: float | None = None
    nome_destinatario: str | None = None
    cep_destino: str | None = None
    endereco_destino: str | None = None
    numero_destino: str | None = None
    complemento_destino: str | None = None
    bairro_destino: str | None = None
    cidade_destino: str | None = None
    uf_destino: str | None = None
    # True quando já existe devolução lançada para este pedido+sku (front esmaece).
    ja_devolvido: bool = False


class AcompanhamentoItemOut(BaseModel):
    """Linha (por ITEM do pedido) da aba Acompanhamento — pedidos hoje em
    'Aguardando Devolução' (83957) no Bling, com cliente e rastreio manual."""

    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    data: datetime | None = None
    # Dia em que o pedido entrou em Aguardando Devolução (carimbo do ingest).
    aguardando_devolucao_data: date | None = None
    dias_em_devolucao: int | None = None
    plataforma: str | None = None
    loja: str | None = None
    cliente: str | None = None
    cidade: str | None = None
    uf: str | None = None
    sku: str | None = None
    produto: str | None = None
    quantidade: int | None = None
    rastreio: str | None = None
    localizacao: str | None = None
    localizacao_data: datetime | None = None
    # True quando o pedido já tem devolução LANÇADA na aba de lançamentos.
    lancada: bool = False


class AcompanhamentoOut(BaseModel):
    items: list[AcompanhamentoItemOut]
    total_pedidos: int


class AcompanhamentoRastreioPatch(BaseModel):
    """Edição inline de rastreio/localização na aba Acompanhamento. Campos
    ausentes não são tocados; string vazia limpa o campo."""

    rastreio: str | None = None
    localizacao: str | None = None


class AcompanhamentoRastreioOut(BaseModel):
    pedido_bling: str
    rastreio: str | None = None
    localizacao: str | None = None
    localizacao_data: datetime | None = None
