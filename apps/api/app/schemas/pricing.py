from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------- pricing accounts

class PricingAccountBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    platform: str
    listing_type: str | None = None
    # Inputs may set the root either via `segment_id` (preferred) or via the
    # legacy `department` slug; outputs always include both for UI compat.
    segment_id: UUID | None = None
    department: str | None = None
    kit_number: int = Field(default=1, ge=1, le=5)
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
    kit_number: int | None = Field(default=None, ge=1, le=5)
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
    description: str | None = None
    model: str | None = None
    ean: str | None = None
    is_active: bool = True
    in_catalog: bool = False
    product_id: UUID | None = None


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
    description: str | None = None
    model: str | None = None
    ean: str | None = None
    is_active: bool | None = None
    in_catalog: bool | None = None
    product_id: UUID | None = None


class PricingProductOut(PricingProductBase):
    model_config = ConfigDict(from_attributes=True)
    sku: str = Field(max_length=2048)
    name: str = Field(max_length=512)
    id: UUID
    user_id: UUID
    created_at: datetime
    updated_at: datetime


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


class PricingOverrideUpsert(PricingOverrideBase):
    pass


class PricingOverrideCellStatus(BaseModel):
    pricing_product_id: UUID
    pricing_account_id: UUID
    cell_status: str


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


class StoreInfoOut(StoreInfoBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    user_id: UUID
    has_password: bool = False
    # Computed fields (mirroring the SSH "Tipo / Tab.Preço / Integração" badges)
    departments: list[str] = []
    has_pricing: bool = False
    has_integration: bool = False
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------- auto-match (9d)

class AutoMatchResult(BaseModel):
    matched: int
    skipped: int
    accounts: list[UUID] = []


class AccountSetDepartmentIn(BaseModel):
    department: str
