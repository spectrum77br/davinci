"""SKU audit (Fase 9d) + shared SKU matching helpers.

Two separate concerns live here so callers don't need to duplicate the
celular base-SKU matching logic:

  * `match_pricing_to_product_keys` — for stock/sales maps.
    Given a list of `pricing_products` and the set of all product SKU keys
    (lowercased), returns `{pricing_product_id: set(matched_product_keys)}`.

  * `find_pricing_for_product_sku` — for the audit (the inverse).
    Given a product SKU and indexes built from the pricing list, returns the
    PricingProduct that covers it (or None).

Matching rules (per `pricing_product.department`):
  - `celular`: each comma piece is treated as a *base* — products whose
    SKU equals the base or starts with `base + '.'` are matched.
    Example: pricing piece `dg078` matches products `dg078`, `dg078.pi`,
    `dg078.ra`, etc.
  - `mala` / `eletro` / others: exact case-insensitive match.
  - `catalogo`: exact match, but pieces containing `+` are skipped (catálogo
    composite SKUs only exist on the products side).
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from uuid import UUID

import structlog
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AuditDismissedSku,
    Integration,
    PricingProduct,
    Product,
    ProductLink,
)

logger = structlog.get_logger()


def _dept_value(pp: PricingProduct) -> str:
    d = pp.department
    return d.value if hasattr(d, "value") else str(d)


def _q2(v: Decimal | None) -> Decimal | None:
    if v is None:
        return None
    return Decimal(v).quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# Shared matching helpers
# ---------------------------------------------------------------------------


def match_pricing_to_product_keys(
    pricing_rows: Iterable[PricingProduct],
    product_keys: Iterable[str],
) -> dict[UUID, set[str]]:
    """Returns {pricing_product_id: {matched product sku keys (lowercased)}}.

    See module docstring for the per-department matching rules.
    """
    keys = {k.strip().lower() for k in product_keys if k}
    by_base: dict[str, set[str]] = {}
    by_exact: dict[str, set[str]] = {}
    for k in keys:
        by_exact.setdefault(k, set()).add(k)
        base = k.split(".")[0]
        if base:
            by_base.setdefault(base, set()).add(k)

    out: dict[UUID, set[str]] = {}
    for pp in pricing_rows:
        dept = _dept_value(pp)
        matched: set[str] = set()
        for piece in (pp.sku or "").split(","):
            key = piece.strip().lower()
            if not key:
                continue
            if dept == "catalogo" and "+" in key:
                continue
            if dept == "celular":
                matched.update(by_base.get(key, set()))
            else:
                matched.update(by_exact.get(key, set()))
        if matched:
            out[pp.id] = matched
    return out


def build_product_lookup(
    pricing_rows: Iterable[PricingProduct],
) -> tuple[dict[str, PricingProduct], dict[str, PricingProduct]]:
    """Returns (exact_map, celular_base_map) used by
    `find_pricing_for_product_sku`. Exact map is filled for every department;
    base map only for celular pieces.
    """
    exact_map: dict[str, PricingProduct] = {}
    base_map: dict[str, PricingProduct] = {}
    for pp in pricing_rows:
        dept = _dept_value(pp)
        for piece in (pp.sku or "").split(","):
            key = piece.strip().lower()
            if not key:
                continue
            if dept == "catalogo" and "+" in key:
                continue
            exact_map.setdefault(key, pp)
            if dept == "celular":
                base_map.setdefault(key, pp)
    return exact_map, base_map


def find_pricing_for_product_sku(
    sku: str,
    exact_map: dict[str, PricingProduct],
    base_map: dict[str, PricingProduct],
) -> PricingProduct | None:
    """Resolves a products.sku to its covering PricingProduct (or None).

    Order: exact → celular base. Caller must have built the maps via
    `build_product_lookup`.
    """
    skl = sku.strip().lower()
    if not skl:
        return None
    exact = exact_map.get(skl)
    if exact is not None:
        return exact
    base = skl.split(".")[0]
    return base_map.get(base)


# ---------------------------------------------------------------------------
# Audit (consumes the helpers above)
# ---------------------------------------------------------------------------


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

    exact_map, base_map = build_product_lookup(pricing_rows)

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
    matched_count = 0
    for p in products:
        sku = (p.sku or "").strip()
        if not sku:
            continue
        is_dismissed = sku in dismissed_skus
        if not include_dismissed and is_dismissed:
            continue

        pp = find_pricing_for_product_sku(sku, exact_map, base_map)
        if pp is not None:
            matched_count += 1
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

    logger.info(
        "pricing.audit.scan",
        user_id=str(user_id),
        products_with_stock=len(products),
        pricing_products=len(pricing_rows),
        products_matched_to_pricing=matched_count,
        pendings=len(out),
        include_dismissed=include_dismissed,
    )
    return out


__all__ = [
    "scan_missing_skus",
    "match_pricing_to_product_keys",
    "build_product_lookup",
    "find_pricing_for_product_sku",
]
