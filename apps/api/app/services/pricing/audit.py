"""SKU audit (Fase 9d) + shared SKU matching helpers.

Two separate concerns live here so callers don't need to duplicate the
celular base-SKU matching logic:

  * `match_pricing_to_product_keys` — for stock/sales maps.
    Given a list of `pricing_products` and the set of all product SKU keys
    (lowercased), returns `{pricing_product_id: set(matched_product_keys)}`.

  * `find_pricing_for_product_sku` — for the audit (the inverse).
    Given a product SKU and indexes built from the pricing list, returns the
    PricingProduct that covers it (or None).

Matching rules (per `pricing_product` root segment slug):
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
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AuditDismissedSku,
    Integration,
    PricingProduct,
    Product,
    ProductLink,
    Segment,
)

logger = structlog.get_logger()


async def _load_segment_roots(session: AsyncSession) -> dict[UUID, str]:
    """Returns {segment_id: root_slug} for every segment in the taxonomy.

    Built fresh per call rather than cached across requests — the taxonomy
    table is tiny and rarely changes. Callers that need it more than once
    per request should pass the result around.
    """
    rows = (await session.execute(select(Segment))).scalars().all()
    by_id = {s.id: s for s in rows}
    out: dict[UUID, str] = {}
    for s in rows:
        cur = s
        seen: set[UUID] = set()
        while cur.parent_id and cur.id not in seen:
            seen.add(cur.id)
            parent = by_id.get(cur.parent_id)
            if parent is None:
                break
            cur = parent
        out[s.id] = cur.slug
    return out


def _dept_value(pp: PricingProduct, segment_roots: dict[UUID, str]) -> str:
    """Returns the root segment slug for a pricing_product."""
    return segment_roots.get(pp.segment_id, "")


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
    segment_roots: dict[UUID, str],
) -> dict[UUID, set[str]]:
    """Returns {pricing_product_id: {matched product sku keys (lowercased)}}.

    `segment_roots` is a `{segment_id: root_slug}` map (e.g. from
    `_load_segment_roots`) used to dispatch the per-root SKU rule.

    Per-dept matching rules:
      * celular: base-SKU (everything before first dot) wins
      * catalogo: exact only; pieces with '+' are skipped
      * mala: bidirectional kit↔piece matching (see below)
      * default: exact only

    Mala bidirectional rule — every dept=mala piece without '+' matches:
      1. its exact key in bling_orders (if any)
      2. every key in the same base whose sizes set ⊇ piece's sizes
         set. Catches:
           * single piece (b045.18) → kits b045.X.18, b045.18.Y, etc.
             (kit sales also consume the individual piece's stock)
           * kit (b045.18.20) → bigger kits b045.X.18.20.Y, etc.
      3. for kit pieces (≥3 parts) WITHOUT exact match, fall back to
         summing each single-size component (b045.18.20 with no exact
         → b045.18 + b045.20). Preserves commit af9465c behavior —
         covers the case where a 2-piece kit didn't sell as kit but
         the individual sizes did.
    """
    keys = {k.strip().lower() for k in product_keys if k}
    by_base: dict[str, set[str]] = {}
    by_exact: dict[str, set[str]] = {}
    # by_base_sizes[base] → list of (full_key, sizes_set) for every
    # bling_order key that follows the base.sizes pattern (single or
    # multi-size). Used by the mala bidirectional rule. Excludes keys
    # with '+' (operator wants those at zero).
    by_base_sizes: dict[str, list[tuple[str, frozenset[str]]]] = {}
    for k in keys:
        by_exact.setdefault(k, set()).add(k)
        parts = k.split(".")
        base = parts[0]
        if base:
            by_base.setdefault(base, set()).add(k)
            if "+" not in k and len(parts) >= 2:
                sizes = frozenset(p for p in parts[1:] if p.isdigit())
                if sizes:
                    by_base_sizes.setdefault(base, []).append((k, sizes))

    out: dict[UUID, set[str]] = {}
    for pp in pricing_rows:
        dept = _dept_value(pp, segment_roots)
        matched: set[str] = set()
        for piece in (pp.sku or "").split(","):
            key = piece.strip().lower()
            if not key:
                continue
            if dept == "catalogo" and "+" in key:
                continue
            if dept == "celular":
                matched.update(by_base.get(key, set()))
                continue

            exact_hit = by_exact.get(key, set())
            matched.update(exact_hit)

            # Mala bidirectional kit↔piece matching. Other depts stop here.
            if dept != "mala" or "+" in key:
                continue

            parts = key.split(".")
            if len(parts) < 2:
                continue
            base = parts[0]
            piece_sizes = frozenset(p for p in parts[1:] if p.isdigit())
            if not base or not piece_sizes:
                continue

            # Step 2: any bling_order key with same base whose sizes ⊇
            # piece's sizes. Skip self (already added via exact_hit).
            for bo_key, bo_sizes in by_base_sizes.get(base, ()):
                if bo_key == key:
                    continue
                if piece_sizes.issubset(bo_sizes):
                    matched.add(bo_key)

            # Step 3 (kit only, exact empty): fall back to single-size
            # components. Existing behavior from commit af9465c — kept
            # so 2-piece kits without exact sale still capture demand
            # from individual size sales.
            if not exact_hit and len(parts) >= 3:
                for size in parts[1:]:
                    if not size.isdigit():
                        continue
                    component_key = f"{base}.{size}"
                    matched.update(by_exact.get(component_key, set()))
        if matched:
            out[pp.id] = matched
    return out


def build_product_lookup(
    pricing_rows: Iterable[PricingProduct],
    segment_roots: dict[UUID, str],
) -> tuple[dict[str, list[PricingProduct]], dict[str, list[PricingProduct]]]:
    """Returns (exact_map, celular_base_map) used by
    `find_pricing_for_product_sku`. Each key maps to a LIST of
    PricingProducts — the same SKU often appears in multiple departments
    (e.g. celular + catalogo), and the audit needs to check ALL of them
    before deciding whether a divergence exists.
    """
    exact_map: dict[str, list[PricingProduct]] = {}
    base_map: dict[str, list[PricingProduct]] = {}
    for pp in pricing_rows:
        dept = _dept_value(pp, segment_roots)
        for piece in (pp.sku or "").split(","):
            key = piece.strip().lower()
            if not key:
                continue
            if dept == "catalogo" and "+" in key:
                continue
            exact_map.setdefault(key, []).append(pp)
            if dept == "celular":
                base_map.setdefault(key, []).append(pp)
    return exact_map, base_map


def find_pricing_for_product_sku(
    sku: str,
    exact_map: dict[str, list[PricingProduct]],
    base_map: dict[str, list[PricingProduct]],
) -> list[PricingProduct]:
    """Resolves a products.sku to EVERY matching PricingProduct.

    Order: exact match wins (returns all departments that exact-match);
    otherwise falls back to celular's base-sku index. Returns [] when
    nothing matches. Caller must have built the maps via
    `build_product_lookup`.
    """
    skl = sku.strip().lower()
    if not skl:
        return []
    exact = exact_map.get(skl)
    if exact:
        return exact
    base = skl.split(".")[0]
    return base_map.get(base, [])


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
    # Only audit products that are active in Bling: situacao='A' (Ativo) or
    # NULL (legacy rows without the flag). Excluded ('E') and inactive ('I')
    # products may still carry residual stock locally but they're no longer
    # sellable, so flagging them as "Fora da tabela" would be noise.
    products = (
        await session.execute(
            select(Product).where(
                and_(
                    Product.stock > 0,
                    or_(
                        Product.situacao == 'A',
                        Product.situacao.is_(None),
                    ),
                )
            )
        )
    ).scalars().all()

    pricing_rows = (
        await session.execute(select(PricingProduct))
    ).scalars().all()

    segment_roots = await _load_segment_roots(session)
    exact_map, base_map = build_product_lookup(pricing_rows, segment_roots)

    link_rows = (
        await session.execute(
            select(ProductLink, Integration.name)
            .outerjoin(Integration, Integration.id == ProductLink.integration_id)
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

        pp_list = find_pricing_for_product_sku(sku, exact_map, base_map)
        if pp_list:
            matched_count += 1
        accounts = sorted(set(links_by_pid.get(p.id, [])))
        issues: list[str] = []

        bling_cost = _q2(p.bling_cost_price)
        pricing_cost: Decimal | None = None
        divergent_depts: list[str] = []

        if not pp_list:
            issues.append("Fora da tabela de preços")
        else:
            # "Custo divergente" only for simple products (formato != 'E')
            # — kits store the total cost in Bling but unit costs in the
            # pricing table, so comparing them is meaningless.
            #
            # When the same SKU appears in multiple departments (celular +
            # catalogo etc.), each row has its own cost_kit1. EVERY row
            # must match the Bling cost (rounded to integer to ignore
            # intentional centavo differences like 41.00 vs 41.20). Any
            # department that doesn't match is reported so the user knows
            # exactly which row to fix.
            is_kit = (p.formato or '').upper() == 'E'
            if not is_kit and bling_cost is not None:
                bling_int = int(round(bling_cost))
                for pp in pp_list:
                    kit1 = _q2(pp.cost_kit1) if pp.cost_kit1 else None
                    if kit1 is None:
                        continue
                    if int(round(kit1)) == bling_int:
                        continue
                    divergent_depts.append(_dept_value(pp, segment_roots))
                    if pricing_cost is None:
                        pricing_cost = kit1
                if divergent_depts:
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
                "divergent_departments": divergent_depts or None,
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
    "_load_segment_roots",
]
