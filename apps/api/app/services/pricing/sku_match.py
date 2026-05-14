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
