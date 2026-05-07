"""SKU audit (Fase 9d).

Identifies SKUs that exist in `listings` but are NOT registered in
`pricing_products` for the same user. UI surfaces these so the operator
can either import the missing rows or dismiss them (`audit_dismissed_skus`).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, distinct, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AuditDismissedSku,
    Listing,
    PricingProduct,
)


async def scan_missing_skus(
    session: AsyncSession,
    *,
    user_id: UUID,
    include_dismissed: bool = False,
) -> list[dict]:
    """Returns rows shaped as
        {sku, listing_count, sample_titles, dismissed}
    sorted by listing_count desc.
    """
    listings_q = select(
        Listing.sku, Listing.title, Listing.platform, Listing.integration_id
    ).where(
        and_(
            Listing.user_id == user_id,
            Listing.sku.isnot(None),
            Listing.sku != "",
        )
    )
    rows = (await session.execute(listings_q)).all()

    pricing_skus = set(
        (
            await session.execute(
                select(distinct(PricingProduct.sku)).where(
                    PricingProduct.user_id == user_id
                )
            )
        ).scalars().all()
    )
    dismissed_skus = set(
        (
            await session.execute(
                select(AuditDismissedSku.sku).where(
                    AuditDismissedSku.user_id == user_id
                )
            )
        ).scalars().all()
    )

    by_sku: dict[str, dict] = {}
    for sku, title, platform, integ_id in rows:
        if sku in pricing_skus:
            continue
        if not include_dismissed and sku in dismissed_skus:
            continue
        bucket = by_sku.setdefault(
            sku,
            {
                "sku": sku,
                "listing_count": 0,
                "platforms": set(),
                "integration_ids": set(),
                "sample_titles": [],
                "dismissed": sku in dismissed_skus,
            },
        )
        bucket["listing_count"] += 1
        bucket["platforms"].add(
            platform.value if hasattr(platform, "value") else platform
        )
        bucket["integration_ids"].add(str(integ_id))
        if len(bucket["sample_titles"]) < 3:
            bucket["sample_titles"].append(title)

    out = [
        {
            "sku": v["sku"],
            "listing_count": v["listing_count"],
            "platforms": sorted(v["platforms"]),
            "integration_ids": sorted(v["integration_ids"]),
            "sample_titles": v["sample_titles"],
            "dismissed": v["dismissed"],
        }
        for v in by_sku.values()
    ]
    out.sort(key=lambda r: (-r["listing_count"], r["sku"]))
    return out
