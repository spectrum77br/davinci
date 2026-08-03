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


def build_match_indexes(
    product_keys: Iterable[str],
) -> tuple[
    dict[str, set[str]],
    dict[str, set[str]],
    dict[str, list[tuple[str, frozenset[str]]]],
]:
    """Build the 3 indexes that `match_one_sku_to_keys` consults.

    Returns (by_exact, by_base_celular, by_base_sizes):
      * by_exact[k]            = {k}                 — lookup helper
      * by_base_celular[base]  = every k.* with that base prefix
      * by_base_sizes[base]    = list of (k, sizes-of-k) for every
                                 base.size[.size...] key (no '+')

    Same set of keys is shared across all dept rules — each rule
    consumes a subset of the indexes. Built once per request.
    """
    keys = {k.strip().lower() for k in product_keys if k}
    by_exact: dict[str, set[str]] = {}
    by_base_celular: dict[str, set[str]] = {}
    by_base_sizes: dict[str, list[tuple[str, frozenset[str]]]] = {}
    for k in keys:
        by_exact.setdefault(k, set()).add(k)
        parts = k.split(".")
        base = parts[0]
        if base:
            by_base_celular.setdefault(base, set()).add(k)
            if "+" not in k and len(parts) >= 2:
                sizes = frozenset(p for p in parts[1:] if p.isdigit())
                if sizes:
                    by_base_sizes.setdefault(base, []).append((k, sizes))
    return by_exact, by_base_celular, by_base_sizes


def match_one_sku_to_keys(
    sku_key: str,
    dept: str,
    by_exact: dict[str, set[str]],
    by_base_celular: dict[str, set[str]],
    by_base_sizes: dict[str, list[tuple[str, frozenset[str]]]],
) -> set[str]:
    """Return the bling_order item_codigo keys that count as sales for
    `sku_key`. Pure per-piece logic — caller handles comma-split,
    aggregation, and dept resolution.

    Per-dept rules:
      * celular  : base-SKU prefix wins (everything before first dot)
      * catalogo : exact only; pieces with '+' are skipped
      * mala     : exact + bidirectional kit↔piece match (see below)
      * default  : exact only

    Mala bidirectional rule (piece without '+'):
      1. exact key (if any)
      2. every key in the same base whose sizes ⊇ piece's sizes —
         single piece (b045.18) catches kits b045.X.18 / b045.18.Y;
         kit (b045.18.20) catches bigger kits b045.X.18.20.Y.
      3. for kit pieces (≥3 parts) WITHOUT exact, fall back to summing
         each single-size component (b045.18.20 with no exact →
         b045.18 + b045.20). Preserves the commit af9465c fallback —
         covers 2-piece kits that don't sell as kits but whose
         individual sizes do.
    """
    key = (sku_key or "").strip().lower()
    if not key:
        return set()
    # Composite SKUs with '+' are zero by operator decision for both
    # catalogo and mala (the two depts that use composite pricing
    # SKUs like `b005.8.12.20.24+a075+bp003+a076`). Other depts
    # default to exact match including '+' since their composites
    # would map 1:1 in bling_orders if they existed.
    if "+" in key and dept in ("catalogo", "mala"):
        return set()
    if dept == "celular":
        return set(by_base_celular.get(key, set()))

    matched: set[str] = set(by_exact.get(key, set()))

    if dept != "mala":
        return matched

    parts = key.split(".")
    if len(parts) < 2:
        return matched
    base = parts[0]
    piece_sizes = frozenset(p for p in parts[1:] if p.isdigit())
    if not base or not piece_sizes:
        return matched

    # Bidirectional superset: any bling_order key with same base whose
    # sizes ⊇ piece's sizes. Skip self (already in matched via exact).
    for bo_key, bo_sizes in by_base_sizes.get(base, ()):
        if bo_key == key:
            continue
        if piece_sizes.issubset(bo_sizes):
            matched.add(bo_key)

    # Kit-only fallback (exact empty + ≥3 parts): single-size components.
    if not by_exact.get(key) and len(parts) >= 3:
        for size in parts[1:]:
            if not size.isdigit():
                continue
            component_key = f"{base}.{size}"
            matched.update(by_exact.get(component_key, set()))

    return matched


def match_pricing_to_product_keys(
    pricing_rows: Iterable[PricingProduct],
    product_keys: Iterable[str],
    segment_roots: dict[UUID, str],
) -> dict[UUID, set[str]]:
    """Returns {pricing_product_id: {matched product sku keys (lowercased)}}.

    Thin wrapper over `match_one_sku_to_keys`: builds the indexes once
    and iterates the pricing_rows × pieces grid, OR-merging per-piece
    matches. Caller dispatches by dept via `segment_roots`.
    """
    by_exact, by_base_celular, by_base_sizes = build_match_indexes(product_keys)
    out: dict[UUID, set[str]] = {}
    for pp in pricing_rows:
        dept = _dept_value(pp, segment_roots)
        matched: set[str] = set()
        for piece in (pp.sku or "").split(","):
            matched |= match_one_sku_to_keys(
                piece, dept, by_exact, by_base_celular, by_base_sizes,
            )
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

    # Dispensar é GLOBAL: um SKU dispensado por qualquer usuário fica oculto
    # para todos. Os endpoints de dismiss/undismiss já operam globalmente, então
    # aqui não filtramos por user_id (senão cada um só enxergaria os próprios).
    dismissed_skus = set(
        (
            await session.execute(
                select(AuditDismissedSku.sku)
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
            # "Custo divergente" só pra produtos SIMPLES. Compostos
            # guardam o custo TOTAL no Bling mas custos unitários por
            # componente na tabela, então comparar não faz sentido.
            # Composto = formato='E' (composto puro no Bling) OU SKU com
            # '+' (convenção local de kit/bundle, ex dg052.ci+a001.ci —
            # esses ficam como formato='S' no Bling mas não são simples).
            #
            # When the same SKU appears in multiple departments (celular +
            # catalogo etc.), each row has its own cost_kit1. EVERY row
            # must match the Bling cost (rounded to integer to ignore
            # intentional centavo differences like 41.00 vs 41.20). Any
            # department that doesn't match is reported so the user knows
            # exactly which row to fix.
            is_kit = (p.formato or '').upper() == 'E'
            has_composite_marker = '+' in (p.sku or '')
            if not is_kit and not has_composite_marker and bling_cost is not None:
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
    "match_one_sku_to_keys",
    "build_match_indexes",
    "build_product_lookup",
    "find_pricing_for_product_sku",
    "_load_segment_roots",
]
