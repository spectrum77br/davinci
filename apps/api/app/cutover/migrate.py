"""Legacy `stocksync` → new `davinci` data migration.

Reads from a side-loaded legacy Postgres schema (typically `stocksync_legacy`
in the same DB) and inserts into the live `davinci` schema. Idempotent per
table only when the target table is empty (use `--reset` to TRUNCATE).

The original `stocksync` schema uses SERIAL ints; the new schema uses UUIDs.
Each row gets a fresh UUID; per-table dicts of `{old_int_id: new_uuid}` are
built up in memory so foreign keys resolve correctly across tables.
"""
# Schema/table names are interpolated into f-strings — they come from
# operator-controlled CLI args (--legacy-schema, --target-schema), not user
# input, and asyncpg does not parameterize identifiers.
# ruff: noqa: S608

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any

import asyncpg

from app.cutover.mappings import (
    DROPPED_PLATFORMS,
    LEGACY_ALERT_SEVERITY,
    LEGACY_ALERT_TYPE,
    LEGACY_LISTING_REQUEST_STATUS,
    LEGACY_LISTING_STATUS,
    LEGACY_PRICING_PLATFORM,
    LEGACY_TO_NEW_PLATFORM,
    LINK_SYNC_DEFAULT,
)
from app.security.cipher import encrypt, encrypt_json

log = logging.getLogger("cutover")

IdMap = dict[int, uuid.UUID]


@dataclass
class MigrationStats:
    table: str
    legacy_count: int = 0
    inserted: int = 0
    skipped: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped += 1
        self.reasons[reason] = self.reasons.get(reason, 0) + 1


@dataclass
class CutoverContext:
    legacy_schema: str
    target_schema: str
    clear_credentials: bool
    src: asyncpg.Connection
    dst: asyncpg.Connection
    users: IdMap = field(default_factory=dict)
    integrations: IdMap = field(default_factory=dict)
    products: IdMap = field(default_factory=dict)
    product_links: IdMap = field(default_factory=dict)
    pricing_products: IdMap = field(default_factory=dict)
    pricing_accounts: IdMap = field(default_factory=dict)
    stats: list[MigrationStats] = field(default_factory=list)


def _new_id() -> uuid.UUID:
    return uuid.uuid4()


def _norm_open_id(legacy_open_id: str | None, email: str | None) -> str:
    if legacy_open_id and legacy_open_id.startswith("email:"):
        return legacy_open_id
    if email:
        return f"email:{email.strip().lower()}"
    raise ValueError("user has neither openId nor email")


def _to_int_or_none(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, int):
        return v
    s = str(v).strip()
    return int(s) if re.fullmatch(r"-?\d+", s) else None


def _parse_daily_time(v: str | None) -> time | None:
    if not v:
        return None
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", v.strip())
    if not m:
        return None
    h, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= h < 24 and 0 <= mm < 60):
        return None
    return time(hour=h, minute=mm)


def _to_utc(dt: datetime | None) -> datetime | None:
    return dt  # asyncpg returns timezone-aware when target column is timestamptz


# --------------------------------------------------------------------------
# Per-table migrations
# --------------------------------------------------------------------------


async def _set_search_path(conn: asyncpg.Connection, schema: str) -> None:
    await conn.execute(f"SET search_path TO {schema}, public")


async def migrate_users(ctx: CutoverContext) -> MigrationStats:
    from app.config import get_settings
    owner_open_id = get_settings().owner_open_id

    s = MigrationStats(table="users")
    rows = await ctx.src.fetch(
        f'SELECT id, "openId", name, email, role, status, "createdAt", "updatedAt", "lastSignedIn" '
        f"FROM {ctx.legacy_schema}.users ORDER BY id"
    )
    s.legacy_count = len(rows)
    for r in rows:
        try:
            open_id = _norm_open_id(r["openId"], r["email"])
        except ValueError:
            s.skip("missing_open_id_and_email")
            continue
        new_id = _new_id()
        ctx.users[r["id"]] = new_id
        status = r["status"] if r["status"] in ("pending", "active", "suspended") else "active"
        role = r["role"] or "user"
        # Owner (spectrum77 / configured owner_open_id) is always admin+active.
        if open_id == owner_open_id:
            role = "admin"
            status = "active"
        await ctx.dst.execute(
            f"INSERT INTO {ctx.target_schema}.users "
            "(id, open_id, email, name, role, status, permissions, last_login_at, "
            " created_at, updated_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,$10) "
            "ON CONFLICT (open_id) DO NOTHING",
            new_id,
            open_id,
            r["email"],
            r["name"],
            role,
            status,
            "{}",
            r["lastSignedIn"],
            r["createdAt"],
            r["updatedAt"],
        )
        s.inserted += 1
    return s


async def migrate_integrations(ctx: CutoverContext) -> MigrationStats:
    s = MigrationStats(table="integrations")
    rows = await ctx.src.fetch(
        f'SELECT id, "userId", platform, name, "isActive", credentials, "lastSyncAt", '
        f'status, "errorMessage", "createdAt", "updatedAt" '
        f"FROM {ctx.legacy_schema}.integrations ORDER BY id"
    )
    s.legacy_count = len(rows)
    for r in rows:
        user_uuid = ctx.users.get(r["userId"])
        if not user_uuid:
            s.skip("orphan_user")
            continue
        platform = LEGACY_TO_NEW_PLATFORM.get(r["platform"])
        if not platform:
            s.skip(f"platform_{r['platform']}_not_supported")
            continue
        new_id = _new_id()
        ctx.integrations[r["id"]] = new_id

        if ctx.clear_credentials:
            blob = encrypt_json({"_cleared_at_cutover": True})
            status = "disconnected"
            last_error = "Credentials cleared at cutover — please reconnect via OAuth"
        else:
            try:
                payload = json.loads(r["credentials"]) if r["credentials"] else {}
            except (TypeError, ValueError):
                payload = {"_legacy_raw": r["credentials"]}
            blob = encrypt_json(payload)
            status = "active" if r["isActive"] else "disconnected"
            last_error = r["errorMessage"]

        await ctx.dst.execute(
            f"INSERT INTO {ctx.target_schema}.integrations "
            "(id, user_id, store_id, platform, name, credentials, status, "
            " last_test_at, last_error, created_at, updated_at) "
            "VALUES ($1,$2,NULL,$3,$4,$5,$6,$7,$8,$9,$10)",
            new_id,
            user_uuid,
            platform,
            r["name"],
            blob,
            status,
            r["lastSyncAt"],
            last_error,
            r["createdAt"],
            r["updatedAt"],
        )
        s.inserted += 1
    return s


async def migrate_products(ctx: CutoverContext) -> MigrationStats:
    s = MigrationStats(table="products")
    rows = await ctx.src.fetch(
        f'SELECT id, "userId", sku, name, "blingId", "blingStock", '
        f'"lowStockThreshold", "lastSyncAt", "createdAt", "updatedAt" '
        f"FROM {ctx.legacy_schema}.products ORDER BY id"
    )
    s.legacy_count = len(rows)
    insert_sql = (
        f"INSERT INTO {ctx.target_schema}.products "
        "(id, user_id, sku, name, stock, min_stock, bling_product_id, "
        " last_imported_at, created_at, updated_at) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)"
    )
    # Dedupe by (user_uuid, sku) so ctx.products stays consistent with the DB.
    by_key: dict[tuple[uuid.UUID, str], uuid.UUID] = {}
    batch: list[tuple] = []
    for r in rows:
        user_uuid = ctx.users.get(r["userId"])
        if not user_uuid:
            s.skip("orphan_user")
            continue
        sku = r["sku"] or f"legacy-{r['id']}"
        key = (user_uuid, sku)
        existing = by_key.get(key)
        if existing is not None:
            ctx.products[r["id"]] = existing
            s.skip("dup_user_sku")
            continue
        new_id = _new_id()
        by_key[key] = new_id
        ctx.products[r["id"]] = new_id
        batch.append((
            new_id, user_uuid, sku,
            r["name"] or f"legacy-{r['id']}",
            r["blingStock"] or 0,
            r["lowStockThreshold"] or 0,
            _to_int_or_none(r["blingId"]),
            r["lastSyncAt"],
            r["createdAt"],
            r["updatedAt"],
        ))
        if len(batch) >= 2000:
            await ctx.dst.executemany(insert_sql, batch)
            s.inserted += len(batch)
            batch = []
    if batch:
        await ctx.dst.executemany(insert_sql, batch)
        s.inserted += len(batch)
    return s


async def migrate_product_links(ctx: CutoverContext) -> MigrationStats:
    s = MigrationStats(table="product_links")
    rows = await ctx.src.fetch(
        f'SELECT id, "userId", "productId", platform, "integrationId", '
        f'"externalId", "variationId", stock, "listingType", "lastSyncAt", '
        f'"suspendedAt", "suspendedReason", "createdAt", "updatedAt" '
        f"FROM {ctx.legacy_schema}.product_links ORDER BY id"
    )
    s.legacy_count = len(rows)
    insert_sql = (
        f"INSERT INTO {ctx.target_schema}.product_links "
        "(id, user_id, product_id, integration_id, store_id, platform, "
        " external_id, variation_id, stock, last_sync_status, last_sync_at, "
        " last_error, created_at, updated_at) "
        "VALUES ($1,$2,$3,$4,NULL,$5,$6,$7,$8,$9,$10,$11,$12,$13) "
        "ON CONFLICT DO NOTHING"
    )
    batch: list[tuple] = []
    for r in rows:
        if r["platform"] in DROPPED_PLATFORMS:
            s.skip(f"platform_{r['platform']}_dropped")
            continue
        user_uuid = ctx.users.get(r["userId"])
        prod_uuid = ctx.products.get(r["productId"])
        intg_uuid = ctx.integrations.get(r["integrationId"])
        if not (user_uuid and prod_uuid and intg_uuid):
            s.skip("orphan_fk")
            continue
        platform = LEGACY_TO_NEW_PLATFORM.get(r["platform"])
        if not platform:
            s.skip(f"platform_{r['platform']}_not_supported")
            continue
        new_id = _new_id()
        ctx.product_links[r["id"]] = new_id
        batch.append((
            new_id, user_uuid, prod_uuid, intg_uuid, platform,
            r["externalId"], r["variationId"], r["stock"],
            LINK_SYNC_DEFAULT, r["lastSyncAt"], r["suspendedReason"],
            r["createdAt"], r["updatedAt"],
        ))
        if len(batch) >= 2000:
            await ctx.dst.executemany(insert_sql, batch)
            s.inserted += len(batch)
            batch = []
    if batch:
        await ctx.dst.executemany(insert_sql, batch)
        s.inserted += len(batch)
    return s


async def migrate_listings(ctx: CutoverContext) -> MigrationStats:
    s = MigrationStats(table="listings")
    rows = await ctx.src.fetch(
        f'SELECT id, "userId", platform, "externalId", sku, title, description, '
        f'price, stock, status, category, "thumbnailUrl", "productId", "rawData", '
        f'"importedAt", "createdAt", "updatedAt" '
        f"FROM {ctx.legacy_schema}.listings ORDER BY id"
    )
    s.legacy_count = len(rows)
    for r in rows:
        platform = LEGACY_TO_NEW_PLATFORM.get(r["platform"])
        if not platform:
            s.skip(f"platform_{r['platform']}_not_supported")
            continue
        user_uuid = ctx.users.get(r["userId"])
        if not user_uuid:
            s.skip("orphan_user")
            continue
        # listings.integration_id is NOT NULL in target — pick *any* integration of
        # this user+platform if the legacy row didn't have one (legacy schema
        # didn't keep the integration link on listings rows).
        intg_uuid = await ctx.dst.fetchval(
            f"SELECT id FROM {ctx.target_schema}.integrations "
            "WHERE user_id = $1 AND platform = $2 LIMIT 1",
            user_uuid,
            platform,
        )
        if not intg_uuid:
            s.skip("no_integration_for_listing")
            continue
        prod_uuid = ctx.products.get(r["productId"]) if r["productId"] else None
        status = LEGACY_LISTING_STATUS.get(r["status"], "active")
        try:
            raw = json.loads(r["rawData"]) if r["rawData"] else {}
        except (TypeError, ValueError):
            raw = {}
        await ctx.dst.execute(
            f"INSERT INTO {ctx.target_schema}.listings "
            "(id, user_id, integration_id, platform, external_id, sku, title, "
            " description, price, stock, status, category, thumbnail_url, "
            " product_id, raw_data, imported_at, created_at, updated_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::jsonb,"
            "        $16,$17,$18) "
            "ON CONFLICT DO NOTHING",
            _new_id(),
            user_uuid,
            intg_uuid,
            platform,
            r["externalId"],
            r["sku"],
            r["title"],
            r["description"],
            r["price"],
            r["stock"],
            status,
            r["category"],
            r["thumbnailUrl"],
            prod_uuid,
            json.dumps(raw),
            r["importedAt"],
            r["createdAt"],
            r["updatedAt"],
        )
        s.inserted += 1
    return s


async def migrate_listing_requests(ctx: CutoverContext) -> MigrationStats:
    s = MigrationStats(table="listing_requests")
    rows = await ctx.src.fetch(
        f'SELECT id, "userId", platform, sku, "productName", description, '
        f'"requestedPrice", category, notes, status, "createdAt", "updatedAt" '
        f"FROM {ctx.legacy_schema}.listing_requests ORDER BY id"
    )
    s.legacy_count = len(rows)
    for r in rows:
        platform = LEGACY_TO_NEW_PLATFORM.get(r["platform"])
        if not platform:
            s.skip(f"platform_{r['platform']}_not_supported")
            continue
        user_uuid = ctx.users.get(r["userId"])
        if not user_uuid:
            s.skip("orphan_user")
            continue
        await ctx.dst.execute(
            f"INSERT INTO {ctx.target_schema}.listing_requests "
            "(id, user_id, platform, sku, product_name, description, "
            " requested_price, category, notes, status, created_at, updated_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)",
            _new_id(),
            user_uuid,
            platform,
            r["sku"],
            r["productName"],
            r["description"],
            r["requestedPrice"],
            r["category"],
            r["notes"],
            LEGACY_LISTING_REQUEST_STATUS.get(r["status"], "pending"),
            r["createdAt"],
            r["updatedAt"],
        )
        s.inserted += 1
    return s


async def migrate_user_settings(ctx: CutoverContext) -> MigrationStats:
    s = MigrationStats(table="user_settings")
    rows = await ctx.src.fetch(
        f'SELECT "userId", "syncIntervalMinutes", "lowStockThreshold", '
        f'"emailNotifications", "autoSync", "dailySyncTime", '
        f'"createdAt", "updatedAt" '
        f"FROM {ctx.legacy_schema}.user_settings"
    )
    s.legacy_count = len(rows)
    for r in rows:
        user_uuid = ctx.users.get(r["userId"])
        if not user_uuid:
            s.skip("orphan_user")
            continue
        await ctx.dst.execute(
            f"INSERT INTO {ctx.target_schema}.user_settings "
            "(user_id, daily_sync_enabled, daily_sync_time, sync_interval_minutes, "
            " low_stock_threshold, notify_email, notify_telegram, notify_daily_sync, "
            " telegram_chat_id, created_at, updated_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,FALSE,TRUE,NULL,$7,$8) "
            "ON CONFLICT (user_id) DO NOTHING",
            user_uuid,
            bool(r["autoSync"]),
            _parse_daily_time(r["dailySyncTime"]),
            r["syncIntervalMinutes"],
            r["lowStockThreshold"],
            bool(r["emailNotifications"]),
            r["createdAt"],
            r["updatedAt"],
        )
        s.inserted += 1
    return s


async def migrate_alerts(ctx: CutoverContext) -> MigrationStats:
    s = MigrationStats(table="alerts")
    rows = await ctx.src.fetch(
        f'SELECT id, "userId", type, severity, title, message, platform, '
        f'"productId", "isRead", "createdAt" '
        f"FROM {ctx.legacy_schema}.alerts ORDER BY id"
    )
    s.legacy_count = len(rows)
    payload_default = "{}"
    batch: list[tuple] = []
    for r in rows:
        user_uuid = ctx.users.get(r["userId"])
        if not user_uuid:
            s.skip("orphan_user")
            continue
        atype = LEGACY_ALERT_TYPE.get(r["type"], "generic")
        sev = LEGACY_ALERT_SEVERITY.get(r["severity"], "info")
        read_at = r["createdAt"] if r["isRead"] else None
        batch.append(
            (_new_id(), user_uuid, atype, sev, r["title"], r["message"],
             payload_default, read_at, r["createdAt"])
        )
        if len(batch) >= 2000:
            await ctx.dst.executemany(
                f"INSERT INTO {ctx.target_schema}.alerts "
                "(id, user_id, type, severity, title, message, payload, read_at, "
                " created_at) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9)",
                batch,
            )
            s.inserted += len(batch)
            batch = []
    if batch:
        await ctx.dst.executemany(
            f"INSERT INTO {ctx.target_schema}.alerts "
            "(id, user_id, type, severity, title, message, payload, read_at, "
            " created_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9)",
            batch,
        )
        s.inserted += len(batch)
    return s


async def migrate_pricing_accounts(ctx: CutoverContext) -> MigrationStats:
    s = MigrationStats(table="pricing_accounts")
    rows = await ctx.src.fetch(
        f'SELECT id, "userId", name, platform, "listingType", department, '
        f'"kitNumber", commission, '
        f'margin1, shipping1, margin2, shipping2, margin3, shipping3, '
        f'margin4, shipping4, margin5, shipping5, '
        f'server, email, password, phone, "shippingAddress", "returnAddress", '
        f'observation, observation2, observation3, "integrationId", '
        f'"sortOrder", "createdAt", "updatedAt" '
        f"FROM {ctx.legacy_schema}.pricing_accounts ORDER BY id"
    )
    s.legacy_count = len(rows)
    for r in rows:
        user_uuid = ctx.users.get(r["userId"])
        if not user_uuid:
            s.skip("orphan_user")
            continue
        platform = LEGACY_PRICING_PLATFORM.get(r["platform"])
        if not platform:
            s.skip(f"platform_{r['platform']}_not_supported")
            continue
        intg_uuid = ctx.integrations.get(r["integrationId"]) if r["integrationId"] else None
        new_id = _new_id()
        ctx.pricing_accounts[r["id"]] = new_id
        password_enc = encrypt(r["password"]) if r["password"] else None
        await ctx.dst.execute(
            f"INSERT INTO {ctx.target_schema}.pricing_accounts "
            "(id, user_id, name, platform, listing_type, department, kit_number, "
            " commission, margin1, shipping1, margin2, shipping2, margin3, shipping3, "
            " margin4, shipping4, margin5, shipping5, server, email, password_enc, "
            " phone, shipping_address, return_address, observation, observation2, "
            " observation3, integration_id, sort_order, created_at, updated_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,"
            "        $18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31)",
            new_id,
            user_uuid,
            r["name"],
            platform,
            r["listingType"],
            r["department"] or "celular",
            r["kitNumber"] or 1,
            r["commission"],
            r["margin1"],
            r["shipping1"],
            r["margin2"],
            r["shipping2"],
            r["margin3"],
            r["shipping3"],
            r["margin4"],
            r["shipping4"],
            r["margin5"],
            r["shipping5"],
            r["server"],
            r["email"],
            password_enc,
            r["phone"],
            r["shippingAddress"],
            r["returnAddress"],
            r["observation"],
            r["observation2"],
            r["observation3"],
            intg_uuid,
            r["sortOrder"] or 0,
            r["createdAt"],
            r["updatedAt"],
        )
        s.inserted += 1
    return s


async def migrate_pricing_products(ctx: CutoverContext) -> MigrationStats:
    s = MigrationStats(table="pricing_products")
    rows = await ctx.src.fetch(
        f'SELECT id, "userId", "productId", sku, name, department, "productType", '
        f'"blingCostPrice", "costKit1", "costKit2", "costKit3", "costKit4", '
        f'description, model, ean, "isActive", "createdAt", "updatedAt" '
        f"FROM {ctx.legacy_schema}.pricing_products ORDER BY id"
    )
    s.legacy_count = len(rows)
    by_key: dict[tuple[uuid.UUID, str], uuid.UUID] = {}
    for r in rows:
        user_uuid = ctx.users.get(r["userId"])
        if not user_uuid:
            s.skip("orphan_user")
            continue
        sku = r["sku"]
        key = (user_uuid, sku)
        existing = by_key.get(key)
        if existing is not None:
            ctx.pricing_products[r["id"]] = existing
            s.skip("dup_user_sku")
            continue
        prod_uuid = ctx.products.get(r["productId"]) if r["productId"] else None
        new_id = _new_id()
        by_key[key] = new_id
        ctx.pricing_products[r["id"]] = new_id
        await ctx.dst.execute(
            f"INSERT INTO {ctx.target_schema}.pricing_products "
            "(id, user_id, product_id, sku, name, department, product_type, "
            " bling_cost_price, cost_kit1, cost_kit2, cost_kit3, cost_kit4, "
            " description, model, ean, is_active, in_catalog, created_at, updated_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,FALSE,$17,$18)",
            new_id,
            user_uuid,
            prod_uuid,
            sku,
            r["name"],
            r["department"] or "celular",
            r["productType"] or 2,
            r["blingCostPrice"],
            r["costKit1"] or 0,
            r["costKit2"],
            r["costKit3"],
            r["costKit4"],
            r["description"],
            r["model"],
            (r["ean"] or "")[:64] or None,
            bool(r["isActive"]),
            r["createdAt"],
            r["updatedAt"],
        )
        s.inserted += 1
    return s


async def migrate_pricing_overrides(ctx: CutoverContext) -> MigrationStats:
    s = MigrationStats(table="pricing_overrides")
    rows = await ctx.src.fetch(
        f'SELECT id, "userId", "pricingProductId", "pricingAccountId", '
        f'"priceOverride", "cellStatus", "createdAt", "updatedAt" '
        f"FROM {ctx.legacy_schema}.pricing_overrides ORDER BY id"
    )
    s.legacy_count = len(rows)
    for r in rows:
        user_uuid = ctx.users.get(r["userId"])
        prod_uuid = ctx.pricing_products.get(r["pricingProductId"])
        acc_uuid = ctx.pricing_accounts.get(r["pricingAccountId"])
        if not (user_uuid and prod_uuid and acc_uuid):
            s.skip("orphan_fk")
            continue
        cell = (r["cellStatus"] or "auto").lower()
        if cell not in ("auto", "manual", "locked", "disabled"):
            cell = "auto"
        await ctx.dst.execute(
            f"INSERT INTO {ctx.target_schema}.pricing_overrides "
            "(id, user_id, pricing_product_id, pricing_account_id, price_override, "
            " cell_status, created_at, updated_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8) "
            "ON CONFLICT (pricing_product_id, pricing_account_id) DO NOTHING",
            _new_id(),
            user_uuid,
            prod_uuid,
            acc_uuid,
            r["priceOverride"],
            cell,
            r["createdAt"],
            r["updatedAt"],
        )
        s.inserted += 1
    return s


async def migrate_store_info(ctx: CutoverContext) -> MigrationStats:
    s = MigrationStats(table="store_info")
    rows = await ctx.src.fetch(
        f'SELECT id, "userId", platform, segment, freight, "cpfName", '
        f'"accountName", server, cnpj, email, observation, "shippingAddress", '
        f'"returnAddress", phone, password, link, "sortOrder", '
        f'"createdAt", "updatedAt" '
        f"FROM {ctx.legacy_schema}.store_info ORDER BY id"
    )
    s.legacy_count = len(rows)
    for r in rows:
        user_uuid = ctx.users.get(r["userId"])
        if not user_uuid:
            s.skip("orphan_user")
            continue
        password_enc = encrypt(r["password"]) if r["password"] else None
        await ctx.dst.execute(
            f"INSERT INTO {ctx.target_schema}.store_info "
            "(id, user_id, platform, segment, freight, cpf_name, account_name, "
            " server, cnpj, email, observation, shipping_address, return_address, "
            " phone, password_enc, link, sort_order, created_at, updated_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)",
            _new_id(),
            user_uuid,
            r["platform"],
            r["segment"],
            r["freight"],
            r["cpfName"],
            r["accountName"],
            r["server"],
            r["cnpj"],
            r["email"],
            r["observation"],
            r["shippingAddress"],
            r["returnAddress"],
            r["phone"],
            password_enc,
            r["link"],
            r["sortOrder"] or 0,
            r["createdAt"],
            r["updatedAt"],
        )
        s.inserted += 1
    return s


async def migrate_audit_dismissed_skus(ctx: CutoverContext) -> MigrationStats:
    s = MigrationStats(table="audit_dismissed_skus")
    rows = await ctx.src.fetch(
        f'SELECT id, "userId", sku, "dismissedAt" '
        f"FROM {ctx.legacy_schema}.dismissed_audit_skus ORDER BY id"
    )
    s.legacy_count = len(rows)
    for r in rows:
        user_uuid = ctx.users.get(r["userId"])
        if not user_uuid:
            s.skip("orphan_user")
            continue
        await ctx.dst.execute(
            f"INSERT INTO {ctx.target_schema}.audit_dismissed_skus "
            "(id, user_id, sku, dismissed_at) VALUES ($1,$2,$3,$4) "
            "ON CONFLICT (user_id, sku) DO NOTHING",
            _new_id(),
            user_uuid,
            r["sku"],
            r["dismissedAt"],
        )
        s.inserted += 1
    return s


# --------------------------------------------------------------------------
# Orchestrator
# --------------------------------------------------------------------------


MIGRATIONS = [
    migrate_users,
    migrate_integrations,
    migrate_products,
    migrate_product_links,
    migrate_listings,
    migrate_listing_requests,
    migrate_user_settings,
    migrate_alerts,
    migrate_pricing_accounts,
    migrate_pricing_products,
    migrate_pricing_overrides,
    migrate_store_info,
    migrate_audit_dismissed_skus,
]

TARGET_TABLES_TO_RESET = [
    "audit_dismissed_skus",
    "store_info",
    "pricing_overrides",
    "pricing_products",
    "pricing_accounts",
    "alerts",
    "user_settings",
    "listing_requests",
    "listings",
    "product_links",
    "products",
    "integrations",
    "users",
]


async def reset_target(conn: asyncpg.Connection, schema: str) -> None:
    """Wipe legacy-derived tables but preserve xlsx-seeded tables (companies,
    cadastros, stores, cadastros_stores). TRUNCATE CASCADE would drag those
    along via FK, so we use ordered DELETE + nullify the FK to users."""
    # Nullify FK from xlsx-seeded tables to users so DELETE on users succeeds
    # via ON DELETE SET NULL (no cascade truncate).
    await conn.execute(f"UPDATE {schema}.companies SET responsavel_id = NULL")
    await conn.execute(f"UPDATE {schema}.cadastros SET responsavel_id = NULL")
    for t in TARGET_TABLES_TO_RESET:
        await conn.execute(f"DELETE FROM {schema}.{t}")


async def run_cutover(
    legacy_url: str,
    target_url: str,
    legacy_schema: str = "stocksync_legacy",
    target_schema: str = "davinci",
    clear_credentials: bool = True,
    reset: bool = False,
) -> list[MigrationStats]:
    src = await asyncpg.connect(
        legacy_url,
        server_settings={"tcp_keepalives_idle": "30"},
        timeout=60,
    )
    dst = await asyncpg.connect(
        target_url,
        server_settings={"tcp_keepalives_idle": "30"},
        timeout=60,
    )
    try:
        await _set_search_path(src, legacy_schema)
        await _set_search_path(dst, target_schema)
        if reset:
            log.warning("resetting target schema %s", target_schema)
            await reset_target(dst, target_schema)
        ctx = CutoverContext(
            legacy_schema=legacy_schema,
            target_schema=target_schema,
            clear_credentials=clear_credentials,
            src=src,
            dst=dst,
        )
        # Per-table transaction so a tunnel drop mid-run only loses one table.
        for fn in MIGRATIONS:
            async with dst.transaction():
                stats = await fn(ctx)
            ctx.stats.append(stats)
            log.info(
                "migrated %s: %d/%d (skipped %d)",
                stats.table,
                stats.inserted,
                stats.legacy_count,
                stats.skipped,
            )
        return ctx.stats
    finally:
        await src.close()
        await dst.close()
