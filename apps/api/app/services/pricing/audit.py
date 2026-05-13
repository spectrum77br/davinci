"""SKU audit (Fase 9d).

Scans `products` (the Bling-imported catalog) and surfaces three kinds of
pendency for SKUs with stock > 0:
  1. "Fora da tabela de preços" — SKU absent from any pricing_products.sku
     (pricing_products.sku may be comma-separated, so each piece is checked).
  2. "Sem anúncio" — no rows in product_links for that product.
  3. "Custo divergente" — product is in pricing_products but bling_cost_price
     differs (rounded to 2 decimals).

Operators can dismiss SKUs (audit_dismissed_skus) so resolved cases stop
showing up.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, distinct, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AuditDismissedSku,
    Integration,
    PricingProduct,
    Product,
    ProductLink,
)


def _q2(v: Decimal | None) -> Decimal | None:
    if v is None:
        return None
    return Decimal(v).quantize(Decimal("0.01"))


async def scan_missing_skus(
    session: AsyncSession,
    *,
    user_id: UUID,
    include_dismissed: bool = False,
) -> list[dict]:
    """Returns rows shaped as
        {sku, title, stock, accounts, account_count, issues, dismissed,
         bling_cost, pricing_cost, listing_count, sample_titles, platforms,
         integration_ids}
    sorted by issue priority (sem anúncio first, then divergente, then fora).
    """
    # Pull catalog state first.
    products = (
        await session.execute(
            select(Product).where(
                and_(Product.user_id == user_id, Product.stock > 0)
            )
        )
    ).scalars().all()

    pricing_rows = (
        await session.execute(
            select(PricingProduct).where(PricingProduct.user_id == user_id)
        )
    ).scalars().all()

    sku_to_pricing: dict[str, PricingProduct] = {}
    for pp in pricing_rows:
        for piece in (pp.sku or "").split(","):
            key = piece.strip().lower()
            if key:
                sku_to_pricing[key] = pp

    # Links + integrations to figure out "Sem anúncio" + show account names.
    link_rows = (
        await session.execute(
            select(ProductLink, Integration.name)
            .outerjoin(Integration, Integration.id == ProductLink.integration_id)
            .where(ProductLink.user_id == user_id)
        )
    ).all()
    links_by_pid: dict[UUID, list[str]] = {}
    for link, integ_name in link_rows:
        bucket = links_by_pid.setdefault(link.product_id, [])
        bucket.append(integ_name or link.platform or "—")

    dismissed_skus = set(
        (
            await session.execute(
                select(AuditDismissedSku.sku).where(
                    AuditDismissedSku.user_id == user_id
                )
            )
        ).scalars().all()
    )

    out: list[dict] = []
    for p in products:
        sku = (p.sku or "").strip()
        if not sku:
            continue
        is_dismissed = sku in dismissed_skus
        if not include_dismissed and is_dismissed:
            continue

        pp = sku_to_pricing.get(sku.lower())
        accounts = sorted(set(links_by_pid.get(p.id, [])))
        issues: list[str] = []

        bling_cost = _q2(p.bling_cost_price)
        pricing_cost: Decimal | None = None

        if pp is None:
            issues.append("Fora da tabela de preços")
        else:
            pricing_cost = _q2(pp.bling_cost_price)
            if bling_cost is not None and pricing_cost is not None and bling_cost != pricing_cost:
                issues.append("Custo divergente")

        if not accounts:
            issues.append("Sem anúncio")

        if not issues:
            continue

        out.append(
            {
                "sku": sku,
                "title": p.name or sku,
                "stock": int(p.stock or 0),
                "accounts": accounts,
                "account_count": len(accounts),
                "issues": issues,
                "dismissed": is_dismissed,
                "bling_cost": str(bling_cost) if bling_cost is not None else None,
                "pricing_cost": str(pricing_cost) if pricing_cost is not None else None,
                # Legacy fields kept so older clients don't break.
                "listing_count": len(accounts),
                "sample_titles": [p.name] if p.name else [],
                "platforms": [],
                "integration_ids": [],
            }
        )

    def _priority(row: dict) -> tuple[int, str]:
        issues = row["issues"]
        if "Sem anúncio" in issues:
            order = 0
        elif "Custo divergente" in issues:
            order = 1
        else:
            order = 2
        return (order, row["sku"])

    out.sort(key=_priority)
    return out


# Keep the old name available for any other callers that might import it.
__all__ = ["scan_missing_skus"]
