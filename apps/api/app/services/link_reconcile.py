"""On-demand SKU reconcile for a single product's marketplace links.

Scenario (dg053.sp → dg053.ci): stock runs out on one physical SKU, so the
operator edits the seller_sku ON the marketplace listing (every account) to
point at the SKU that still has stock. DaVinci's link keeps pointing at the OLD
product (stock 0), so the listing shows zero. Clicking "recarregar" on the OLD
product row re-reads each listing's CURRENT seller_sku and, when it no longer
matches, MOVES the link onto the product that owns the new SKU (re-point, not
delete/recreate) — then the normal stock push runs from the right product.

This runs ONLY from the per-product reload endpoint (routers.sync.sync_product),
never from auto-link or sync-all. Only platforms whose seller_sku is an editable
field on the listing are in scope (ML/Shopee/TikTok). Bling self-links are the
source of truth; Amazon's seller-sku IS the immutable listing key; Temu has no
read adapter — all skipped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

import structlog
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Integration, IntegrationPlatform, Product, ProductLink, User
from app.security.cipher import decrypt_json, encrypt_json
from app.services.auto_link import _norm_sku, _SkuIndex
from app.services.marketplaces.factory import client_for

logger = structlog.get_logger()

# Platforms where the seller_sku is a mutable field on the listing (so it can
# drift from the product the link points at). Everything else is left alone.
_RECONCILE_PLATFORMS: frozenset[IntegrationPlatform] = frozenset(
    {IntegrationPlatform.ML, IntegrationPlatform.SHOPEE, IntegrationPlatform.TIKTOK}
)


@dataclass(slots=True)
class ReconcileMove:
    link_id: UUID
    integration_id: UUID
    platform: str
    external_id: str
    variation_id: str | None
    from_product_id: UUID
    from_sku: str
    to_product_id: UUID
    to_sku: str


@dataclass(slots=True)
class ReconcileReport:
    checked: int = 0
    unreadable: int = 0
    moves: list[ReconcileMove] = field(default_factory=list)
    excedent_deleted: list[UUID] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)

    @property
    def moved_product_ids(self) -> set[UUID]:
        return {m.to_product_id for m in self.moves}


async def reconcile_product_links(
    session: AsyncSession,
    *,
    user: User,
    product: Product,
    only_integration_ids: list[UUID] | None = None,
) -> ReconcileReport:
    """Re-read the current seller_sku of each in-scope link on `product` and
    re-point any whose SKU now belongs to a different product. Mutates
    `product_links.product_id/external_sku/listing_title` in-place and flushes;
    the caller commits. Never touches links on other platforms."""
    report = ReconcileReport()

    stmt = select(ProductLink).where(
        and_(
            ProductLink.product_id == product.id,
            ProductLink.platform != IntegrationPlatform.BLING,
        )
    )
    if only_integration_ids:
        stmt = stmt.where(ProductLink.integration_id.in_(only_integration_ids))
    links = [
        link
        for link in (await session.execute(stmt)).scalars().all()
        if link.platform in _RECONCILE_PLATFORMS
    ]
    if not links:
        return report

    # SKU → product with the SAME ambiguity semantics as auto-link: a SKU shared
    # by two products is "ambiguo" and does NOT move (can't pick a target).
    products = (
        await session.execute(select(Product).where(Product.user_id == user.id))
    ).scalars().all()
    sku_index = _SkuIndex(products)

    _client_cache: dict[UUID, object] = {}

    async def _client(integration_id: UUID):
        if integration_id in _client_cache:
            return _client_cache[integration_id]
        integ = await session.get(Integration, integration_id)
        if integ is None:
            _client_cache[integration_id] = None
            return None
        creds = decrypt_json(integ.credentials) if integ.credentials else {}

        async def _persist(new_creds: dict, _integ: Integration = integ) -> None:
            _integ.credentials = encrypt_json(new_creds)
            await session.commit()

        c = client_for(
            integ.platform, creds, on_token_refresh=_persist, integration_id=integ.id
        )
        _client_cache[integration_id] = c
        return c

    for link in links:
        report.checked += 1
        client = await _client(link.integration_id)
        if client is None or not hasattr(client, "get_listing_snapshot"):
            report.unreadable += 1
            continue
        try:
            snap = await client.get_listing_snapshot(link)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "reconcile_snapshot_failed", link_id=str(link.id), err=str(e)[:200]
            )
            snap = None
        cur_sku = ((snap or {}).get("sku") or "").strip()
        if not cur_sku:
            # Couldn't read a usable SKU (network blip, listing gone, SKU blank)
            # → leave the link exactly as-is; the normal push still runs.
            report.unreadable += 1
            continue
        if _norm_sku(cur_sku) == _norm_sku(product.sku):
            continue  # listing still matches this product — nothing to move

        target, motivo = sku_index.resolve(cur_sku)
        if target is None:
            report.warnings.append(
                {
                    "code": "produto_novo_ausente" if motivo == "not_found" else "sku_ambiguo",
                    "sku": cur_sku,
                    "link_id": str(link.id),
                    "integration_id": str(link.integration_id),
                    "external_id": link.external_id,
                }
            )
            continue
        if target.id == link.product_id:
            continue  # already correct

        # Defensive: the unique index (user, platform, integration, external_id,
        # COALESCE(variation_id,'')) already guarantees a single row per identity
        # regardless of product_id, so a re-point never collides. But if a broken
        # state ever left a twin on the target, drop it before re-pointing.
        vid = link.variation_id or ""
        excedent = (
            await session.execute(
                select(ProductLink).where(
                    and_(
                        ProductLink.product_id == target.id,
                        ProductLink.integration_id == link.integration_id,
                        ProductLink.platform == link.platform,
                        ProductLink.external_id == link.external_id,
                        func.coalesce(ProductLink.variation_id, "") == vid,
                        ProductLink.id != link.id,
                    )
                )
            )
        ).scalars().all()
        for dup in excedent:
            await session.delete(dup)
            report.excedent_deleted.append(dup.id)
        if excedent:
            await session.flush()

        report.moves.append(
            ReconcileMove(
                link_id=link.id,
                integration_id=link.integration_id,
                platform=link.platform.value,
                external_id=link.external_id,
                variation_id=link.variation_id,
                from_product_id=product.id,
                from_sku=product.sku,
                to_product_id=target.id,
                to_sku=cur_sku,
            )
        )
        # Re-point: the row moves off the old product and onto the new one.
        # Its identity tuple is unchanged, so no unique-index conflict.
        link.product_id = target.id
        link.external_sku = cur_sku
        if snap.get("title"):
            link.listing_title = snap["title"]

    await session.flush()
    logger.info(
        "reconcile_done",
        product_id=str(product.id),
        checked=report.checked,
        moved=len(report.moves),
        excedent_deleted=len(report.excedent_deleted),
        warnings=len(report.warnings),
        unreadable=report.unreadable,
    )
    return report
