"""Load a MySQL/TiDB Stock Sync Hub dump into the side schema `stocksync_legacy`.

The dump is plain MySQL SQL (CREATE TABLE + INSERT INTO batches). We rebuild
the schema in Postgres with permissive types (text/bigint/timestamptz/jsonb)
and stream INSERT VALUES tuples through a small character-level parser so
asyncpg gets native Python types — no need for a MySQL server.

Once loaded, run `python -m app.cutover.cli migrate` to ETL into davinci.
"""
# ruff: noqa: S608

from __future__ import annotations

import argparse
import asyncio
import logging
import re
from collections.abc import Iterator
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import asyncpg

from app.config import get_settings

log = logging.getLogger("load_mysql_dump")

LEGACY_SCHEMA = "stocksync_legacy"


# Pre-defined PG schema for tables consumed by app.cutover.migrate. Keeps
# camelCase column names (quoted) so existing migrate.py SELECTs still work.
TABLE_DDL: dict[str, str] = {
    "users": """
        CREATE TABLE {schema}.users (
            id            bigint PRIMARY KEY,
            "openId"      text NOT NULL,
            name          text,
            email         text,
            "loginMethod" text,
            role          text NOT NULL DEFAULT 'user',
            status        text NOT NULL DEFAULT 'active',
            "createdAt"   timestamptz NOT NULL DEFAULT now(),
            "updatedAt"   timestamptz NOT NULL DEFAULT now(),
            "lastSignedIn" timestamptz
        )
    """,
    "integrations": """
        CREATE TABLE {schema}.integrations (
            id             bigint PRIMARY KEY,
            "userId"       bigint NOT NULL,
            platform       text NOT NULL,
            name           text NOT NULL,
            "isActive"     smallint NOT NULL DEFAULT 1,
            credentials    text NOT NULL,
            "lastSyncAt"   timestamptz,
            status         text NOT NULL DEFAULT 'disconnected',
            "errorMessage" text,
            "createdAt"    timestamptz NOT NULL DEFAULT now(),
            "updatedAt"    timestamptz NOT NULL DEFAULT now()
        )
    """,
    "products": """
        CREATE TABLE {schema}.products (
            id                  bigint PRIMARY KEY,
            "userId"            bigint NOT NULL,
            sku                 text,
            name                text,
            "blingId"           text,
            "blingStock"        bigint,
            "lowStockThreshold" bigint,
            "lastSyncAt"        timestamptz,
            "createdAt"         timestamptz NOT NULL DEFAULT now(),
            "updatedAt"         timestamptz NOT NULL DEFAULT now()
        )
    """,
    "product_links": """
        CREATE TABLE {schema}.product_links (
            id                bigint PRIMARY KEY,
            "userId"          bigint NOT NULL,
            "productId"       bigint NOT NULL,
            platform          text NOT NULL,
            "integrationId"   bigint,
            "externalId"      text,
            "variationId"     text,
            stock             bigint,
            "listingType"     text,
            "lastSyncAt"      timestamptz,
            "suspendedAt"     timestamptz,
            "suspendedReason" text,
            "createdAt"       timestamptz NOT NULL DEFAULT now(),
            "updatedAt"       timestamptz NOT NULL DEFAULT now()
        )
    """,
    "listings": """
        CREATE TABLE {schema}.listings (
            id              bigint PRIMARY KEY,
            "userId"        bigint NOT NULL,
            platform        text NOT NULL,
            "externalId"    text,
            sku             text,
            title           text,
            description     text,
            price           numeric,
            stock           bigint,
            status          text,
            category        text,
            "thumbnailUrl"  text,
            "productId"     bigint,
            "rawData"       text,
            "importedAt"    timestamptz,
            "createdAt"     timestamptz NOT NULL DEFAULT now(),
            "updatedAt"     timestamptz NOT NULL DEFAULT now()
        )
    """,
    "listing_requests": """
        CREATE TABLE {schema}.listing_requests (
            id              bigint PRIMARY KEY,
            "userId"        bigint NOT NULL,
            platform        text,
            sku             text,
            "productName"   text,
            description     text,
            "requestedPrice" numeric,
            category        text,
            notes           text,
            status          text,
            "createdAt"     timestamptz NOT NULL DEFAULT now(),
            "updatedAt"     timestamptz NOT NULL DEFAULT now()
        )
    """,
    "user_settings": """
        CREATE TABLE {schema}.user_settings (
            id                    bigint PRIMARY KEY,
            "userId"              bigint NOT NULL,
            "syncIntervalMinutes" bigint,
            "lowStockThreshold"   bigint,
            "emailNotifications"  smallint,
            "inAppNotifications"  smallint,
            "notifyOnSyncError"   smallint,
            "notifyOnLowStock"    smallint,
            "notifyOnDiscrepancy" smallint,
            "autoSync"            smallint,
            "createdAt"           timestamptz NOT NULL DEFAULT now(),
            "updatedAt"           timestamptz NOT NULL DEFAULT now(),
            "dailySyncTime"       text
        )
    """,
    "alerts": """
        CREATE TABLE {schema}.alerts (
            id          bigint PRIMARY KEY,
            "userId"    bigint NOT NULL,
            type        text,
            severity    text,
            title       text,
            message     text,
            platform    text,
            "productId" bigint,
            "isRead"    smallint,
            "createdAt" timestamptz NOT NULL DEFAULT now()
        )
    """,
    "pricing_accounts": """
        CREATE TABLE {schema}.pricing_accounts (
            id                bigint PRIMARY KEY,
            "userId"          bigint NOT NULL,
            name              text,
            platform          text,
            "listingType"     text,
            department        text,
            "kitNumber"       bigint,
            commission        numeric,
            margin1 numeric, shipping1 numeric,
            margin2 numeric, shipping2 numeric,
            margin3 numeric, shipping3 numeric,
            margin4 numeric, shipping4 numeric,
            margin5 numeric, shipping5 numeric,
            server text, email text, password text, phone text,
            "shippingAddress" text, "returnAddress" text,
            observation text, observation2 text, observation3 text,
            "integrationId" bigint,
            "sortOrder" bigint,
            "createdAt" timestamptz NOT NULL DEFAULT now(),
            "updatedAt" timestamptz NOT NULL DEFAULT now()
        )
    """,
    "pricing_products": """
        CREATE TABLE {schema}.pricing_products (
            id              bigint PRIMARY KEY,
            "userId"        bigint NOT NULL,
            "productId"     bigint,
            sku             text,
            name            text,
            department      text,
            "productType"   bigint,
            "blingCostPrice" numeric,
            "costKit1"      numeric,
            "costKit2"      numeric,
            "costKit3"      numeric,
            "costKit4"      numeric,
            description     text,
            model           text,
            ean             text,
            "isActive"      smallint,
            "createdAt"     timestamptz NOT NULL DEFAULT now(),
            "updatedAt"     timestamptz NOT NULL DEFAULT now()
        )
    """,
    "pricing_overrides": """
        CREATE TABLE {schema}.pricing_overrides (
            id                  bigint PRIMARY KEY,
            "userId"            bigint NOT NULL,
            "pricingProductId"  bigint NOT NULL,
            "pricingAccountId"  bigint NOT NULL,
            "priceOverride"     numeric,
            "cellStatus"        text,
            "createdAt"         timestamptz NOT NULL DEFAULT now(),
            "updatedAt"         timestamptz NOT NULL DEFAULT now()
        )
    """,
    "store_info": """
        CREATE TABLE {schema}.store_info (
            id                bigint PRIMARY KEY,
            "userId"          bigint NOT NULL,
            platform          text,
            segment           text,
            freight           text,
            "cpfName"         text,
            "accountName"     text,
            server            text,
            cnpj              text,
            email             text,
            observation       text,
            "shippingAddress" text,
            "returnAddress"   text,
            phone             text,
            password          text,
            link              text,
            "sortOrder"       bigint,
            "createdAt"       timestamptz NOT NULL DEFAULT now(),
            "updatedAt"       timestamptz NOT NULL DEFAULT now()
        )
    """,
    "dismissed_audit_skus": """
        CREATE TABLE {schema}.dismissed_audit_skus (
            id            bigint PRIMARY KEY,
            "userId"      bigint NOT NULL,
            sku           text NOT NULL,
            "dismissedAt" timestamptz NOT NULL DEFAULT now()
        )
    """,
}

# Tables we don't ETL: skip loading entirely.
SKIP_TABLES = {
    "__drizzle_migrations",
    "price_update_logs",
    "sync_logs",
    "sync_queue",
}


# --------------------------------------------------------------------------
# Dump parsing
# --------------------------------------------------------------------------


_INSERT_HEAD_RE = re.compile(r"^INSERT\s+INTO\s+`([^`]+)`\s*\(([^)]+)\)\s*VALUES\s*", re.IGNORECASE)


def _parse_columns(col_list: str) -> list[str]:
    return [c.strip().strip("`") for c in col_list.split(",")]


def _parse_value_tuple(s: str, start: int) -> tuple[list[Any], int]:
    """Parse one `(v1, v2, ...)` starting at position `start` (which must be `(`).

    Returns (values, index_after_closing_paren).
    """
    assert s[start] == "("
    values: list[Any] = []
    i = start + 1
    n = len(s)
    while i < n:
        # Skip whitespace
        while i < n and s[i] in " \t\r\n":
            i += 1
        if s[i] == ")":
            return values, i + 1
        # Parse one value
        if s[i] == "'":
            # String literal — handle backslash escapes and '' (rare in MySQL dumps)
            buf: list[str] = []
            i += 1
            while i < n:
                ch = s[i]
                if ch == "\\" and i + 1 < n:
                    nxt = s[i + 1]
                    buf.append({
                        "n": "\n", "t": "\t", "r": "\r",
                        "0": "\x00", "Z": "\x1a",
                    }.get(nxt, nxt))
                    i += 2
                    continue
                if ch == "'":
                    # MySQL also allows '' for a literal quote inside strings
                    if i + 1 < n and s[i + 1] == "'":
                        buf.append("'")
                        i += 2
                        continue
                    i += 1
                    break
                buf.append(ch)
                i += 1
            values.append("".join(buf))
        elif s[i:i + 4].upper() == "NULL" and (i + 4 >= n or not s[i + 4].isalnum()):
            values.append(None)
            i += 4
        else:
            # Numeric / boolean / unquoted token until comma or close paren
            j = i
            while j < n and s[j] not in ",)":
                j += 1
            tok = s[i:j].strip()
            if tok == "":
                raise ValueError(f"empty token at pos {i}")
            try:
                values.append(int(tok))
            except ValueError:
                try:
                    values.append(float(tok))
                except ValueError:
                    # Bare identifier like TRUE/FALSE — coerce
                    if tok.upper() == "TRUE":
                        values.append(1)
                    elif tok.upper() == "FALSE":
                        values.append(0)
                    else:
                        values.append(tok)
            i = j
        # Optional comma
        while i < n and s[i] in " \t\r\n":
            i += 1
        if i < n and s[i] == ",":
            i += 1
    raise ValueError("unterminated tuple")


def iter_insert_batches(path: Path) -> Iterator[tuple[str, list[str], list[list[Any]]]]:
    """Yield (table, columns, rows) for each INSERT batch in the dump."""
    with path.open("r", encoding="utf-8", errors="replace") as f:
        buf: list[str] = []
        in_insert = False
        table = ""
        cols: list[str] = []
        for line in f:
            if not in_insert:
                m = _INSERT_HEAD_RE.match(line)
                if m:
                    table = m.group(1)
                    cols = _parse_columns(m.group(2))
                    rest = line[m.end():]
                    if table in SKIP_TABLES:
                        # Drain until we see a line ending with `;` (statement end).
                        if rest.rstrip().endswith(";"):
                            continue
                        # Otherwise consume subsequent lines until terminator.
                        for cont in f:
                            if cont.rstrip().endswith(";"):
                                break
                        continue
                    in_insert = True
                    buf = [rest]
                continue
            buf.append(line)
            if line.rstrip().endswith(";"):
                # Parse all tuples in buf
                blob = "".join(buf).rstrip()
                if blob.endswith(";"):
                    blob = blob[:-1]
                rows: list[list[Any]] = []
                i = 0
                n = len(blob)
                while i < n:
                    while i < n and blob[i] in " \t\r\n,":
                        i += 1
                    if i >= n:
                        break
                    if blob[i] != "(":
                        raise ValueError(f"expected ( at pos {i} in {table}")
                    vals, i = _parse_value_tuple(blob, i)
                    rows.append(vals)
                yield table, cols, rows
                in_insert = False
                buf = []


# --------------------------------------------------------------------------
# Loader
# --------------------------------------------------------------------------


async def init_schema(conn: asyncpg.Connection, schema: str, *, drop: bool) -> None:
    if drop:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    for tbl, ddl in TABLE_DDL.items():
        await conn.execute(ddl.format(schema=f'"{schema}"'))
        log.info("created %s.%s", schema, tbl)


def _coerce_ts(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip()
    if not s or s in ("0000-00-00 00:00:00", "0000-00-00"):
        return None
    # MySQL dump: 'YYYY-MM-DD HH:MM:SS' (or with fraction). datetime.fromisoformat
    # accepts space separator since 3.11. Treat as UTC since dumps are UTC-naive.
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _coerce_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    s = str(v).strip()
    if not s or s == "0000-00-00":
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _coerce_numeric(v: Any) -> Decimal | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    s = str(v).strip()
    if not s:
        return None
    try:
        return Decimal(s)
    except (ValueError, ArithmeticError):
        return None


_COERCERS: dict[str, Callable[[Any], Any]] = {
    "timestamp with time zone": _coerce_ts,
    "timestamp without time zone": _coerce_ts,
    "date": _coerce_date,
    "numeric": _coerce_numeric,
}


_TYPE_CACHE: dict[str, dict[str, str]] = {}


async def _column_types(conn: asyncpg.Connection, schema: str, table: str) -> dict[str, str]:
    key = f"{schema}.{table}"
    if key in _TYPE_CACHE:
        return _TYPE_CACHE[key]
    rows = await conn.fetch(
        """
        SELECT column_name, data_type FROM information_schema.columns
         WHERE table_schema = $1 AND table_name = $2
        """,
        schema, table,
    )
    out = {r["column_name"]: r["data_type"] for r in rows}
    _TYPE_CACHE[key] = out
    return out


async def insert_batch(
    conn: asyncpg.Connection,
    schema: str,
    table: str,
    cols: list[str],
    rows: list[list[Any]],
) -> int:
    """Bulk INSERT via copy_records_to_table — coerce strings into native types
    where the target column needs it (timestamp, date, numeric)."""
    if not rows:
        return 0
    types = await _column_types(conn, schema, table)
    keep_idx = [i for i, c in enumerate(cols) if c in types]
    pruned_cols = [cols[i] for i in keep_idx]
    coercers = [_COERCERS.get(types[c]) for c in pruned_cols]
    pruned_rows = [
        tuple(
            (coercers[k](r[ki]) if coercers[k] else r[ki])
            for k, ki in enumerate(keep_idx)
        )
        for r in rows
    ]
    await conn.copy_records_to_table(
        table, schema_name=schema, columns=pruned_cols, records=pruned_rows
    )
    return len(rows)


async def run(dump_path: Path, *, drop: bool) -> dict[str, int]:
    settings = get_settings()
    raw_url = settings.database_url.replace("+asyncpg", "")

    conn = await asyncpg.connect(raw_url)
    counts: dict[str, int] = {}
    try:
        await init_schema(conn, LEGACY_SCHEMA, drop=drop)
        async with conn.transaction():
            for table, cols, rows in iter_insert_batches(dump_path):
                if table not in TABLE_DDL:
                    log.debug("skip unknown table %s", table)
                    continue
                inserted = await insert_batch(conn, LEGACY_SCHEMA, table, cols, rows)
                counts[table] = counts.get(table, 0) + inserted
                if counts[table] % 50_000 < inserted:
                    log.info("%s: %d rows so far", table, counts[table])
    finally:
        await conn.close()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dump", required=True, type=Path)
    parser.add_argument("--drop", action="store_true", help="drop+recreate stocksync_legacy schema")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    counts = asyncio.run(run(args.dump, drop=args.drop))
    total = sum(counts.values())
    log.info("loaded %d rows total across %d tables", total, len(counts))
    for t, n in sorted(counts.items()):
        log.info("  %s: %d", t, n)


if __name__ == "__main__":
    main()
