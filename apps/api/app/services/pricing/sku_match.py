"""SSH→DaVinci SKU bridge for pricing_product → product matching.

SSH `pricing_products.sku` is a comma-joined list of *base* codes (e.g.
"t031,t032"). DaVinci `products.sku` keeps Bling's full SKU including a
trailing variant suffix and optional bundle parts, e.g. `t031.sa`,
`t031.sa+a001.sa`, `b084.14.20`. A base code "t031" should match any
product whose SKU is `t031`, `t031.<anything>`, or `t031+<anything>`.

This module provides the prefix index used by both the pricing grid and
the push service to walk that relation efficiently.
"""

from __future__ import annotations

from typing import Iterable
from uuid import UUID


def sku_prefixes(sku: str) -> Iterable[str]:
    """Yield every SKU prefix whose right boundary is `.`/`+` or end-of-
    string. So `t031.sa+a001.sa` yields `t031`, `t031.sa`,
    `t031.sa+a001`, and `t031.sa+a001.sa`. Used to map a base code like
    "t031" to every Bling-decorated variant that shares it.
    """
    if not sku:
        return
    yield sku
    for i, ch in enumerate(sku):
        if ch in ".+" and i > 0:
            yield sku[:i]


def build_prefix_index(
    products: Iterable[tuple[UUID, str]],
) -> dict[str, list[UUID]]:
    """Build {prefix → [product_id, ...]} from `(id, sku)` rows. Lookup a
    pricing variant by direct key access; an empty list (missing key)
    means there's no davinci.products row for that base code."""
    out: dict[str, list[UUID]] = {}
    for pid, sku in products:
        for prefix in sku_prefixes(sku):
            out.setdefault(prefix, []).append(pid)
    return out


def variants_of(pricing_product_sku: str | None) -> list[str]:
    """Split a SSH-style comma-joined SKU into the individual base codes,
    dropping whitespace and empty entries that the SSH dump leaves behind
    (e.g. the trailing comma in "t031,t032,")."""
    if not pricing_product_sku:
        return []
    return [s.strip() for s in pricing_product_sku.split(",") if s.strip()]


def resolve_product_ids(
    pricing_product_sku: str | None,
    prefix_index: dict[str, list[UUID]],
) -> list[UUID]:
    """Return every davinci.products.id reachable from the variants in a
    pricing_product.sku, deduplicated."""
    seen: set[UUID] = set()
    out: list[UUID] = []
    for v in variants_of(pricing_product_sku):
        for pid in prefix_index.get(v, ()):
            if pid not in seen:
                seen.add(pid)
                out.append(pid)
    return out


# =============================================================================
# SSH-style department-aware match for push.
# =============================================================================
#
# SSH applies different rules per department when deciding which marketplace
# listings should receive the price for a given pricing_product:
#
#   celular   → ONLY listings whose SKU is a kit (contains "+"). Match the
#               base code (before ".") of the main SKU (before "+") against
#               the pricing_product variants. Non-kit listings belong to
#               the catalogo department, not celular.
#   mala      → Each size is its own listing. Match the main SKU (before
#               "+") exactly against the pricing_product variants — the
#               pricing_product stores full Bling SKUs already.
#   catalogo  → Only single-product listings (no "+"). Exact full-SKU match.
#   eletro    → Same shape as celular (kit listings); spec didn't call it
#               out separately so we keep the celular contract.


def _main_sku(sku: str) -> str:
    return sku.split("+", 1)[0]


def _base_sku(sku: str) -> str:
    return sku.split(".", 1)[0]


def _matches_celular(product_sku: str, pp_variants: set[str]) -> bool:
    if "+" not in product_sku:
        return False
    return _base_sku(_main_sku(product_sku)) in pp_variants


def _matches_mala(product_sku: str, pp_variants: set[str]) -> bool:
    return _main_sku(product_sku) in pp_variants


def _matches_catalogo(product_sku: str, pp_variants: set[str]) -> bool:
    if "+" in product_sku:
        return False
    return product_sku in pp_variants


_DEPT_MATCHERS = {
    "celular": _matches_celular,
    "eletro": _matches_celular,
    "mala": _matches_mala,
    "catalogo": _matches_catalogo,
}


def filter_products_by_department(
    department: str,
    pricing_product_sku: str | None,
    candidate_products: Iterable[tuple[UUID, str]],
) -> list[UUID]:
    """Apply the SSH department-specific rule, returning every davinci.product.id
    whose SKU is eligible to receive the pricing_product's price."""
    pp_variants = set(variants_of(pricing_product_sku))
    if not pp_variants:
        return []
    matcher = _DEPT_MATCHERS.get(department, _matches_celular)
    out: list[UUID] = []
    for pid, sku in candidate_products:
        if sku and matcher(sku, pp_variants):
            out.append(pid)
    return out


# =============================================================================
# listingType bridge: SSH stores "ml classico"/"ml premium" on the account
# but DaVinci's product_links.listing_type carries ML's raw values.
# =============================================================================

_ML_LISTING_TYPE_MAP = {
    "ml classico": "gold_special",
    "ml clássico": "gold_special",
    "classico": "gold_special",
    "clássico": "gold_special",
    "ml premium": "gold_pro",
    "premium": "gold_pro",
}


def ml_listing_type_for_account(account_listing_type: str | None) -> str | None:
    """Translate a pricing_account.listing_type ("ml classico" / "ml premium")
    into the ML listing_type stored on product_links ("gold_special" /
    "gold_pro"). Returns None when no filter should apply."""
    if not account_listing_type:
        return None
    key = account_listing_type.strip().lower()
    return _ML_LISTING_TYPE_MAP.get(key)


# =============================================================================
# Push-time link dedup.
# =============================================================================
#
# ML and Amazon: price is per listing → keep one link per external_id.
# Shopee and TikTok: price is per variation → keep one per (external_id, variation_id).


def dedup_links_for_push(platform: str, links):  # type: ignore[no-untyped-def]
    if platform in ("ml", "amazon"):
        key_fn = lambda link: link.external_id  # noqa: E731
    else:
        key_fn = lambda link: (link.external_id, link.variation_id or "")  # noqa: E731
    seen: set = set()
    out = []
    for link in links:
        k = key_fn(link)
        if k in seen:
            continue
        seen.add(k)
        out.append(link)
    return out
