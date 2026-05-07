"""Count comparison between legacy `stocksync` schema and new `davinci` schema."""
# ruff: noqa: S608

from __future__ import annotations

from dataclasses import dataclass

import asyncpg

# (legacy_table, new_table). Pairs whose counts must match (modulo dropped rows
# noted in the cutover stats).
COUNT_PAIRS = [
    ("users", "users"),
    ("integrations", "integrations"),
    ("products", "products"),
    ("product_links", "product_links"),
    ("listings", "listings"),
    ("listing_requests", "listing_requests"),
    ("alerts", "alerts"),
    ("user_settings", "user_settings"),
    ("pricing_accounts", "pricing_accounts"),
    ("pricing_products", "pricing_products"),
    ("pricing_overrides", "pricing_overrides"),
    ("store_info", "store_info"),
    ("dismissed_audit_skus", "audit_dismissed_skus"),
]


@dataclass
class CountResult:
    table: str
    legacy: int
    new: int

    @property
    def diff(self) -> int:
        return self.legacy - self.new

    @property
    def ok(self) -> bool:
        return self.legacy == self.new


async def _count(conn: asyncpg.Connection, schema: str, table: str) -> int:
    val = await conn.fetchval(f"SELECT count(*) FROM {schema}.{table}")
    return int(val or 0)


async def compare_counts(
    legacy_url: str,
    target_url: str,
    legacy_schema: str = "stocksync_legacy",
    target_schema: str = "davinci",
) -> list[CountResult]:
    src = await asyncpg.connect(legacy_url)
    dst = await asyncpg.connect(target_url)
    try:
        results: list[CountResult] = []
        for legacy_t, new_t in COUNT_PAIRS:
            legacy_count = await _count(src, legacy_schema, legacy_t)
            new_count = await _count(dst, target_schema, new_t)
            results.append(CountResult(table=new_t, legacy=legacy_count, new=new_count))
        return results
    finally:
        await src.close()
        await dst.close()


def render_table(results: list[CountResult]) -> str:
    rows = ["| table | legacy | new | diff | ok |", "|---|---:|---:|---:|---|"]
    for r in results:
        rows.append(
            f"| {r.table} | {r.legacy} | {r.new} | {r.diff} | "
            f"{'✓' if r.ok else '✗'} |"
        )
    return "\n".join(rows)
