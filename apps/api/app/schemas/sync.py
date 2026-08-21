from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class SyncProductIn(BaseModel):
    product_id: UUID


class SyncProductBody(BaseModel):
    integration_ids: list[UUID] | None = None
    # On the per-product "recarregar" click we first re-read each listing's
    # current seller_sku and re-point links whose SKU moved to another product
    # (see services.link_reconcile). Defaults on; callers can disable to get a
    # pure stock push with no re-pointing.
    reconcile: bool = True


class ReloadLinkMove(BaseModel):
    """One re-pointed link: the anúncio's SKU changed on the marketplace and
    the link moved to the product that now owns that SKU."""

    link_id: UUID
    platform: str
    external_id: str
    variation_id: str | None = None
    from_sku: str
    to_sku: str
    to_product_id: UUID
    to_product_sku: str | None = None
    to_product_name: str | None = None


class ReloadLinksOut(BaseModel):
    """Result of the per-product "Recarregar Vínculo" action."""

    job_id: UUID | None = None
    checked: int
    moved: int
    unreadable: int
    warnings: int
    moves: list[ReloadLinkMove]
    warnings_detail: list[dict] = []


class AnuncioVariacaoOut(BaseModel):
    """Uma variação (SKU) do anúncio pesquisado por ID."""

    variation_id: str | None = None
    variacao: str | None = None
    sku: str | None = None
    # vinculado | pronto | trocado | sem_sku | sku_ambiguo | sem_produto
    # ("trocado" = já tem vínculo, mas o SKU atual do anúncio casa com OUTRO
    # produto — o vincular move o link pra ele)
    estado: str
    listing_type: str | None = None
    product_id: UUID | None = None
    produto_nome: str | None = None
    estoque_bling: int | None = None
    estoque_anuncio: int | None = None
    link_id: UUID | None = None
    # Só no estado "trocado": o SKU antigo que o vínculo ainda carrega.
    sku_anterior: str | None = None


class AnuncioResumoOut(BaseModel):
    total: int = 0
    prontos: int = 0
    ja_vinculados: int = 0
    sem_sku: int = 0
    sku_ambiguo: int = 0
    sem_produto: int = 0
    trocados: int = 0


class AnuncioLookupOut(BaseModel):
    """Anúncio achado pelo ID, com todas as variações agrupadas embaixo."""

    encontrado: bool
    motivo: str | None = None
    external_id: str
    plataforma: str | None = None
    integration_id: UUID | None = None
    conta: str | None = None
    titulo: str | None = None
    # "marketplace" = veio da API do canal | "local" = montado dos vínculos
    origem: str | None = None
    resumo: AnuncioResumoOut = AnuncioResumoOut()
    variacoes: list[AnuncioVariacaoOut] = []


class AnuncioSyncIn(BaseModel):
    external_id: str
    integration_id: UUID
    # Cria os vínculos que faltam antes de empurrar o estoque (mesma regra do
    # Vincular Automático, só que restrita a este anúncio).
    vincular: bool = True


class SyncAllIn(BaseModel):
    integration_ids: list[UUID] | None = None
    product_ids: list[UUID] | None = None
    # Bypasses the low-stock-only filter used by the cron-driven daily sync.
    # When the UI explicitly clicks "sync all", users expect every product to
    # push to marketplaces — not just the ones near stockout.
    include_all_stock: bool = False
    # `force=True` makes the mass sync bypass the marketplace-side safety guards
    # (notably ML's B1 zero-guard and the paused/closed short-circuit), exactly
    # like the individual per-product sync. Needed to re-push stock to listings
    # ML auto-paused when their stock hit zero — the default (force=False) skips
    # paused/closed listings and never reactivates them.
    force: bool = False


class SyncLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    job_id: UUID | None = None
    product_id: UUID | None = None
    product_link_id: UUID | None = None
    integration_id: UUID | None = None
    store_id: UUID | None = None
    platform: str | None = None
    action: str
    status: str
    qty_before: int | None = None
    qty_after: int | None = None
    error_code: str | None = None
    error_detail: str | None = None
    payload: dict


class SyncLogPage(BaseModel):
    items: list[SyncLogOut]
    total: int
    limit: int
    offset: int


class SyncLogStats(BaseModel):
    window_hours: int
    ok: int
    skipped: int
    retryable: int
    fatal: int
    requires_review: int
    by_platform: dict[str, dict[str, int]]
