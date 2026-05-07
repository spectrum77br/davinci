"""Competitor price search (Fase 9d).

Hits ML public search (no auth required) and caches results in-process
for 5 minutes per query — keeps the pricing UI snappy without touching
the database. Per defaults aprovados.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()

ML_PUBLIC_SEARCH = "https://api.mercadolibre.com/sites/MLB/search"
TTL_SECONDS = 300


@dataclass(slots=True)
class CompetitorEntry:
    item_id: str
    title: str
    price: float
    currency: str
    permalink: str
    seller_id: int | None
    condition: str | None
    sold_quantity: int | None
    available_quantity: int | None
    thumbnail: str | None


_cache: dict[str, tuple[float, list[CompetitorEntry]]] = {}


def _normalize(raw: dict[str, Any]) -> CompetitorEntry:
    return CompetitorEntry(
        item_id=str(raw.get("id", "")),
        title=str(raw.get("title", "")),
        price=float(raw.get("price") or 0),
        currency=str(raw.get("currency_id", "BRL")),
        permalink=str(raw.get("permalink", "")),
        seller_id=(raw.get("seller") or {}).get("id"),
        condition=raw.get("condition"),
        sold_quantity=raw.get("sold_quantity"),
        available_quantity=raw.get("available_quantity"),
        thumbnail=raw.get("thumbnail"),
    )


async def search_competitors(query: str, *, limit: int = 20) -> list[CompetitorEntry]:
    q = (query or "").strip()
    if not q:
        return []
    now = time.time()
    cached = _cache.get(q)
    if cached and (now - cached[0]) < TTL_SECONDS:
        return cached[1][:limit]

    async with httpx.AsyncClient(timeout=15.0) as cli:
        try:
            r = await cli.get(
                ML_PUBLIC_SEARCH, params={"q": q, "limit": min(limit, 50)}
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("competitor_search_failed", q=q, err=str(e))
            return []

    payload = r.json() or {}
    results = [_normalize(x) for x in (payload.get("results") or [])]
    _cache[q] = (now, results)
    # GC: keep the cache small.
    if len(_cache) > 256:
        oldest = sorted(_cache.items(), key=lambda kv: kv[1][0])[: len(_cache) - 200]
        for k, _ in oldest:
            _cache.pop(k, None)
    return results[:limit]


def cache_clear() -> None:
    """Test helper."""
    _cache.clear()
