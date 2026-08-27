from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.users import STOCK_TAGS

# --------------------------------------------------------------- pricing accounts

class PricingAccountBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    platform: str
    listing_type: str | None = None
    # Inputs may set the root either via `segment_id` (preferred) or via the
    # legacy `department` slug; outputs always include both for UI compat.
    segment_id: UUID | None = None
    department: str | None = None
    kit_number: int = Field(default=1, ge=1, le=8)
    commission: Decimal | None = None
    margin1: Decimal | None = None
    shipping1: Decimal | None = None
    margin2: Decimal | None = None
    shipping2: Decimal | None = None
    margin3: Decimal | None = None
    shipping3: Decimal | None = None
    margin4: Decimal | None = None
    shipping4: Decimal | None = None
    margin5: Decimal | None = None
    shipping5: Decimal | None = None
    server: str | None = None
    email: str | None = None
    phone: str | None = None
    shipping_address: str | None = None
    return_address: str | None = None
    observation: str | None = None
    observation2: str | None = None
    observation3: str | None = None
    discount: str | None = None
    affiliate: str | None = None
    ads: str | None = None
    coupon: str | None = None
    offer: str | None = None
    store_info_id: UUID | None = None
    integration_id: UUID | None = None
    sort_order: int = 0
    # Slot ↔ segment binding. Each slot{N}_segment_id pins the segment that
    # margin{N}/shipping{N} apply to (e.g. slot1=Acessórios, slot2=Diversos).
    # NULL means "not assigned"; UI falls back to TYPE_HEADERS ordering when
    # rendering. Mirrors the columns added by migration 0048.
    slot1_segment_id: UUID | None = None
    slot2_segment_id: UUID | None = None
    slot3_segment_id: UUID | None = None
    slot4_segment_id: UUID | None = None
    slot5_segment_id: UUID | None = None


class PricingAccountCreate(PricingAccountBase):
    password: str | None = None


class PricingAccountPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    platform: str | None = None
    listing_type: str | None = None
    segment_id: UUID | None = None
    department: str | None = None
    kit_number: int | None = Field(default=None, ge=1, le=8)
    commission: Decimal | None = None
    margin1: Decimal | None = None
    shipping1: Decimal | None = None
    margin2: Decimal | None = None
    shipping2: Decimal | None = None
    margin3: Decimal | None = None
    shipping3: Decimal | None = None
    margin4: Decimal | None = None
    shipping4: Decimal | None = None
    margin5: Decimal | None = None
    shipping5: Decimal | None = None
    server: str | None = None
    email: str | None = None
    password: str | None = None
    phone: str | None = None
    shipping_address: str | None = None
    return_address: str | None = None
    observation: str | None = None
    observation2: str | None = None
    observation3: str | None = None
    discount: str | None = None
    affiliate: str | None = None
    ads: str | None = None
    coupon: str | None = None
    offer: str | None = None
    store_info_id: UUID | None = None
    integration_id: UUID | None = None
    sort_order: int | None = None
    slot1_segment_id: UUID | None = None
    slot2_segment_id: UUID | None = None
    slot3_segment_id: UUID | None = None
    slot4_segment_id: UUID | None = None
    slot5_segment_id: UUID | None = None


class PricingAccountOut(PricingAccountBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    user_id: UUID
    has_password: bool = False
    created_at: datetime
    updated_at: datetime
    # Resolved segment names for each slot (read-only; router fills these).
    slot1_segment_name: str | None = None
    slot2_segment_name: str | None = None
    slot3_segment_name: str | None = None
    slot4_segment_name: str | None = None
    slot5_segment_name: str | None = None


# --------------------------------------------------------------- pricing products

def _norm_prioridade_estoque(v: str | None) -> str | None:
    """Normaliza a tag de prioridade de estoque: lowercase, vazio→None.

    O conjunto válido é SUFFIX_TAGS (ci/pi/ra/sa/sp/us/cd) de
    services/sku_tags.py — os sufixos FÍSICOS de SKU (dg053.ci, dg053.sp…).
    fake/mala/eletro/insumos ficam de fora: são grupos de operador, não
    sufixos trocáveis num código de produto.
    """
    from app.services.sku_tags import SUFFIX_TAGS

    if v is None:
        return None
    tag = v.strip().lower().lstrip(".")
    if not tag:
        return None
    if tag not in SUFFIX_TAGS:
        raise ValueError(
            f"prioridade_estoque deve ser uma de {', '.join(SUFFIX_TAGS)}"
        )
    return tag


class PricingProductBase(BaseModel):
    sku: str = Field(min_length=1, max_length=2048)
    name: str = Field(min_length=1, max_length=512)
    # Either `segment_id` (preferred, must be a leaf) or the legacy
    # (`department` + `product_type`) pair. Router resolves to segment_id.
    segment_id: UUID | None = None
    department: str | None = None
    product_type: int | None = None
    bling_cost_price: Decimal | None = None
    cost_kit1: Decimal = Decimal("0")
    cost_kit2: Decimal | None = None
    cost_kit3: Decimal | None = None
    cost_kit4: Decimal | None = None
    cost_kit5: Decimal | None = None
    cost_kit6: Decimal | None = None
    cost_kit7: Decimal | None = None
    cost_kit8: Decimal | None = None
    description: str | None = None
    model: str | None = None
    ean: str | None = None
    is_active: bool = True
    in_catalog: bool = False
    # Link das fotos do produto (pasta do MEGA com todas as cores).
    fotos_url: str | None = None
    # Caminho da pasta na conta MEGA (gerido pela sincronização/upload).
    fotos_path: str | None = None
    product_id: UUID | None = None
    # Tag de estoque prioritária (Eduardo 2026-08-27: "a tag que eu colocar
    # la, o sku com a tag, ja deve trocar, porque a prioridade e ele").
    prioridade_estoque: str | None = None

    @field_validator("prioridade_estoque")
    @classmethod
    def _valida_prioridade(cls, v: str | None) -> str | None:
        return _norm_prioridade_estoque(v)


class PricingProductCreate(PricingProductBase):
    pass


class PricingProductPatch(BaseModel):
    sku: str | None = Field(default=None, min_length=1, max_length=2048)
    name: str | None = Field(default=None, min_length=1, max_length=512)
    segment_id: UUID | None = None
    department: str | None = None
    product_type: int | None = None
    bling_cost_price: Decimal | None = None
    cost_kit1: Decimal | None = None
    cost_kit2: Decimal | None = None
    cost_kit3: Decimal | None = None
    cost_kit4: Decimal | None = None
    cost_kit5: Decimal | None = None
    cost_kit6: Decimal | None = None
    cost_kit7: Decimal | None = None
    cost_kit8: Decimal | None = None
    description: str | None = None
    model: str | None = None
    ean: str | None = None
    is_active: bool | None = None
    in_catalog: bool | None = None
    fotos_url: str | None = None
    product_id: UUID | None = None
    prioridade_estoque: str | None = None

    @field_validator("prioridade_estoque")
    @classmethod
    def _valida_prioridade(cls, v: str | None) -> str | None:
        return _norm_prioridade_estoque(v)


class PricingProductOut(PricingProductBase):
    model_config = ConfigDict(from_attributes=True)
    sku: str = Field(max_length=2048)
    name: str = Field(max_length=512)
    id: UUID
    user_id: UUID
    created_at: datetime
    updated_at: datetime
    # Contagem de mídias na pasta do MEGA (NULL = nunca contado).
    fotos_count: int | None = None
    videos_count: int | None = None


class PricingProductImportItem(BaseModel):
    """Lenient row used by /products/import — empty SKUs are skipped, not rejected."""
    sku: str = ""
    name: str = ""
    segment_id: UUID | None = None
    department: str | None = None
    product_type: int | None = None
    bling_cost_price: Decimal | None = None
    cost_kit1: Decimal = Decimal("0")
    cost_kit2: Decimal | None = None
    cost_kit3: Decimal | None = None
    cost_kit4: Decimal | None = None
    cost_kit5: Decimal | None = None
    cost_kit6: Decimal | None = None
    cost_kit7: Decimal | None = None
    cost_kit8: Decimal | None = None
    description: str | None = None
    model: str | None = None
    ean: str | None = None
    is_active: bool = True
    in_catalog: bool = False
    product_id: UUID | None = None


class PricingProductImport(BaseModel):
    """Bulk import payload (idempotent on (user_id, sku))."""
    items: list[PricingProductImportItem]


class PricingProductImportResult(BaseModel):
    created: int
    updated: int
    skipped: int


# -------------------------------------------------------------- pricing overrides

class PricingOverrideBase(BaseModel):
    pricing_product_id: UUID
    pricing_account_id: UUID
    price_override: Decimal | None = None
    cell_status: str = "auto"
    # Optional on the upsert path: if absent we preserve whatever's
    # already stored. The dedicated /overrides/cell-color endpoint is
    # the canonical way to set it.
    cell_color: str | None = None


class PricingOverrideUpsert(PricingOverrideBase):
    pass


class PricingOverrideCellStatus(BaseModel):
    pricing_product_id: UUID
    pricing_account_id: UUID
    cell_status: str


class PricingOverrideCellColor(BaseModel):
    pricing_product_id: UUID
    pricing_account_id: UUID
    cell_color: str | None = None  # None = clear the highlight


class PricingOverrideOut(PricingOverrideBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    user_id: UUID
    created_at: datetime
    updated_at: datetime


# ----------------------------------------------------------------- push + grid

class PricingPushIn(BaseModel):
    pricing_account_id: UUID
    pricing_product_id: UUID


class PricingPushBatchIn(BaseModel):
    items: list[PricingPushIn]


class PricingPushItemOut(BaseModel):
    pricing_account_id: UUID
    pricing_product_id: UUID
    ok: bool
    code: str
    detail: str | None = None
    price: Decimal | None = None
    item_id: str | None = None
    variation_id: str | None = None
    cached: bool = False


class PricingPushOut(BaseModel):
    results: list[PricingPushItemOut]


class PricingGridCell(BaseModel):
    pricing_account_id: UUID
    pricing_product_id: UUID
    price: Decimal | None = None
    source: str  # computed | override | locked | disabled | missing_inputs
    cell_status: str = "auto"
    has_override: bool = False
    cell_color: str | None = None


class PricingGridOut(BaseModel):
    accounts: list[PricingAccountOut]
    products: list[PricingProductOut]
    cells: list[PricingGridCell]


# ----------------------------------------------------------------- catalog (9c)

class PricingCatalogListingItem(BaseModel):
    """ML catalog listing as seen by the pricing module — slimmer than the
    full Listing schema; only carries what the pricing UI needs."""
    id: UUID
    integration_id: UUID
    external_id: str
    sku: str | None = None
    title: str
    price: int | None = None
    status: str
    in_catalog: bool = True


# ----------------------------------------------------------------- bulk push (9c)

class PricingPushReportIn(BaseModel):
    """Manual report send (PRD pricing.sendPushReport)."""
    summary: str
    chat_id: str | None = None


# ----------------------------------------------------------------- audit (9d)

class SkuAuditRow(BaseModel):
    sku: str
    title: str | None = None
    stock: int | None = None
    accounts: list[str] = []
    account_count: int = 0
    issues: list[str] = []
    bling_cost: str | None = None
    pricing_cost: str | None = None
    # Legacy/back-compat fields (kept so older callers don't break).
    listing_count: int = 0
    platforms: list[str] = []
    integration_ids: list[str] = []
    sample_titles: list[str] = []
    dismissed: bool = False


# ---------------------------------------------------------- competitor (9d)

class CompetitorPriceRow(BaseModel):
    item_id: str
    title: str
    price: float
    currency: str
    permalink: str
    seller_id: int | None = None
    condition: str | None = None
    sold_quantity: int | None = None
    available_quantity: int | None = None
    thumbnail: str | None = None


# ----------------------------------------------------------------- store_info (9d)

class StoreExcecao(BaseModel):
    """Regra de exceção de envio da loja (campo "Exceções" da tela Lojas).

    A UF vem do campo "Restrição" da loja (uf_restrictions) — a regra
    bloqueia o envio automático quando o destino está nas UFs da Restrição e:
      - tipo "valor":   o valor total do pedido é >= `valor`;
      - tipo "sku":     algum item tem um dos SKUs em `termos`;
      - tipo "palavra": o nome de algum item contém uma palavra de `termos`.
    (Regras antigas salvas com `uf` seguem válidas — a chave é ignorada.)
    """

    tipo: Literal["valor", "sku", "palavra"]
    valor: float | None = None
    termos: list[str] | None = None


def normaliza_etiqueta_horarios(v: str | None) -> str | None:
    """"HH:MM" separados por vírgula, ordenados e sem repetir.

    Aceita "10:00" / "10h" / "1000" e devolve None quando vazio (= contínuo:
    a etiqueta é impressa assim que a NF fecha).
    """
    if v is None:
        return None
    saida: set[str] = set()
    for bruto in str(v).replace(";", ",").split(","):
        txt = bruto.strip().lower().replace("h", ":")
        if not txt:
            continue
        if ":" not in txt and txt.isdigit() and len(txt) in (3, 4):
            txt = f"{txt[:-2]}:{txt[-2:]}"
        hora, _, minuto = txt.partition(":")
        minuto = minuto or "0"
        if not hora.isdigit() or not minuto.isdigit():
            raise ValueError(f"horário inválido: {bruto}")
        h, m = int(hora), int(minuto)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError(f"horário inválido: {bruto}")
        saida.add(f"{h:02d}:{m:02d}")
    return ", ".join(sorted(saida)) or None


def normaliza_hora_sabado(v: str | None) -> str | None:
    """Um único "HH:MM" (BRT). Vazio = a loja não emite no sábado."""
    saida = normaliza_etiqueta_horarios(v)
    if saida and "," in saida:
        raise ValueError("informe um único horário para o sábado")
    return saida


def normaliza_faturador_por_tipo(v: dict | None) -> dict[str, str] | None:
    """Faturador POR TIPO (migration 0228): {"celular": "<uuid>", …}.

    Valores como UUID em string (a coluna é JSONB — UUID objeto quebraria o
    json.dumps do driver). Chaves vazias/valores vazios caem fora; dict vazio
    vira None (= regra única, nf_faturador_id).
    """
    if not v:
        return None
    saida: dict[str, str] = {}
    for chave, valor in v.items():
        slug = str(chave or "").strip().lower()
        if not slug or not valor:
            continue
        try:
            saida[slug] = str(UUID(str(valor)))
        except ValueError as exc:  # noqa: PERF203
            raise ValueError(f"faturador inválido para {slug}") from exc
    return saida or None


def normaliza_tags_estoque(v: str | None) -> str | None:
    """Slugs de estoque separados por vírgula, na ordem canônica de STOCK_TAGS.

    Ex. "SP, pi" → "pi, sp". Vazio = nenhum estoque marcado.
    """
    if v is None:
        return None
    escolhidas = {t.strip().lower() for t in str(v).replace(";", ",").split(",") if t.strip()}
    invalidas = escolhidas - set(STOCK_TAGS)
    if invalidas:
        raise ValueError(f"tag de estoque inválida: {', '.join(sorted(invalidas))}")
    return ", ".join(t for t in STOCK_TAGS if t in escolhidas) or None


class StoreInfoBase(BaseModel):
    platform: str = Field(min_length=1, max_length=64)
    segment: str | None = None
    freight: str | None = None
    cpf_name: str | None = None
    account_name: str | None = None
    server: str | None = None
    cnpj: str | None = None
    email: str | None = None
    observation: str | None = None
    shipping_address: str | None = None
    return_address: str | None = None
    phone: str | None = None
    link: str | None = None
    integration_id: UUID | None = None
    sort_order: int = 0
    # Lojas screen extras (migration 0043).
    bling_store_id: str | None = None
    upseseller: bool | None = None
    duoker: bool | None = None
    uf_restrictions: list[str] | None = None
    # Exceções de envio por loja (migration 0216) — ver StoreExcecao.
    excecoes: list[StoreExcecao] | None = None
    # Equipe de Vendas (migration 0136). Número inteiro; NULL = sem equipe.
    sales_team: int | None = None
    # NF automáticas (migration 0196): cadastros Faturador/Etiqueta/Impressão.
    nf_faturador_id: UUID | None = None
    # Faturador POR TIPO (migration 0228) — contas com 2+ tipos na coluna
    # Tipo: {"celular": "<uuid>", "eletro": "<uuid>"}. None = regra única.
    nf_faturador_por_tipo: dict[str, str] | None = None
    # Faturador da NF PRODUTO (migration 0226) — coluna "Faturador produto".
    nf_faturador_produto_id: UUID | None = None
    nf_etiqueta_id: UUID | None = None
    nf_impressao_id: UUID | None = None
    # Horários (BRT) em que as etiquetas da loja são impressas (migration
    # 0222), ex. "10:00, 14:00". Vazio = contínuo.
    etiqueta_horarios: str | None = None
    # Sábado (migration 0223) — só Mercado Livre. Correios segue contínuo;
    # agência emite uma vez neste horário, só nos estoques marcados.
    etiqueta_sabado_horario: str | None = None
    etiqueta_sabado_tags: str | None = None

    _norm_horarios = field_validator("etiqueta_horarios")(normaliza_etiqueta_horarios)
    _norm_sabado_hora = field_validator("etiqueta_sabado_horario")(normaliza_hora_sabado)
    _norm_sabado_tags = field_validator("etiqueta_sabado_tags")(normaliza_tags_estoque)
    _norm_fat_tipo = field_validator("nf_faturador_por_tipo")(normaliza_faturador_por_tipo)


class StoreInfoCreate(StoreInfoBase):
    password: str | None = None


class StoreInfoPatch(BaseModel):
    platform: str | None = None
    segment: str | None = None
    freight: str | None = None
    cpf_name: str | None = None
    account_name: str | None = None
    server: str | None = None
    cnpj: str | None = None
    email: str | None = None
    observation: str | None = None
    shipping_address: str | None = None
    return_address: str | None = None
    phone: str | None = None
    password: str | None = None
    link: str | None = None
    integration_id: UUID | None = None
    sort_order: int | None = None
    bling_store_id: str | None = None
    upseseller: bool | None = None
    duoker: bool | None = None
    uf_restrictions: list[str] | None = None
    excecoes: list[StoreExcecao] | None = None
    sales_team: int | None = None
    nf_faturador_id: UUID | None = None
    nf_faturador_por_tipo: dict[str, str] | None = None
    nf_faturador_produto_id: UUID | None = None
    nf_etiqueta_id: UUID | None = None
    nf_impressao_id: UUID | None = None
    etiqueta_horarios: str | None = None
    etiqueta_sabado_horario: str | None = None
    etiqueta_sabado_tags: str | None = None

    _norm_horarios = field_validator("etiqueta_horarios")(normaliza_etiqueta_horarios)
    _norm_sabado_hora = field_validator("etiqueta_sabado_horario")(normaliza_hora_sabado)
    _norm_sabado_tags = field_validator("etiqueta_sabado_tags")(normaliza_tags_estoque)
    _norm_fat_tipo = field_validator("nf_faturador_por_tipo")(normaliza_faturador_por_tipo)


class StoreInfoOut(StoreInfoBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    user_id: UUID
    has_password: bool = False
    # Computed fields (mirroring the SSH "Tipo / Tab.Preço / Integração" badges)
    departments: list[str] = []
    has_pricing: bool = False
    has_integration: bool = False
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------- auto-match (9d)

class AutoMatchResult(BaseModel):
    matched: int
    skipped: int
    accounts: list[UUID] = []


class AccountSetDepartmentIn(BaseModel):
    department: str
