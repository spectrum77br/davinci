"""One-shot SSH→DaVinci pricing reimport.

TRUNCATE-and-reload of pricing_accounts, pricing_products, pricing_overrides
and non-bling product_links for the spectrum77 tenant from the JSON dumps
under /dumps (mount the host folder to /dumps in the container).

Usage:
    docker compose exec -e DUMP_DIR=/dumps api \
        python -m app.cutover.import_ssh_full
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DV_USER_ID = UUID("6b234ea7-434b-418b-9ae5-64c6ffbcc82d")  # spectrum77@tuta.com

# department_slug -> root segment uuid (used by pricing_accounts)
ROOT_SEGMENT_MAP: dict[str, UUID] = {
    "celular": UUID("5b0ad162-27bb-40e1-8255-a04083d70c99"),
    "mala": UUID("36ab1527-3a82-41d7-bcad-c160f6a6f410"),
    "eletro": UUID("7864254c-ca31-4969-af3c-3a604bfe5805"),
    "catalogo": UUID("cb60cab4-0c23-40a6-8ab6-90b9841ac0b7"),
}

# (department_slug, productType 1..5) -> leaf segment uuid (used by pricing_products)
SEGMENT_MAP: dict[tuple[str, int], UUID] = {
    ("celular", 1): UUID("cd121cbe-4725-4226-8908-06fbbd5e3597"),
    ("celular", 2): UUID("5c3c4054-a372-4d2b-9a35-aab04f9abe55"),
    ("celular", 3): UUID("8904b854-a4ab-40a6-b859-f3ce16e606c2"),
    ("celular", 4): UUID("fdfcbe7d-2c2a-4393-8655-b6aa5df8c3c4"),
    ("celular", 5): UUID("1e9e7d6a-4a79-4ea2-b04e-32ec3dcc9ae1"),
    ("mala", 1): UUID("1d9a0288-a5e8-48ea-a0e4-cb083903a92a"),
    ("mala", 2): UUID("30888d66-29a2-47a7-95c4-d338fb53fc0c"),
    ("mala", 3): UUID("f9706763-6a66-4f70-afcf-9d82005878e3"),
    ("mala", 4): UUID("8d68ae73-8414-47f0-a33b-48b6215c6db7"),
    ("mala", 5): UUID("13ab5678-2e7e-41a5-8264-1aafafe4f039"),
    ("eletro", 1): UUID("3f3798f5-7953-4996-aa12-85f1dde45997"),
    ("eletro", 2): UUID("0c5c7954-eadf-4da5-b16d-04ae541cee41"),
    ("eletro", 3): UUID("64fea537-b568-4318-8f28-96098efd14e2"),
    ("eletro", 4): UUID("58802997-017f-4c3e-9682-cc875ba14c63"),
    ("eletro", 5): UUID("cc2dd669-f941-40c3-806d-34c0a6075d1f"),
    ("catalogo", 1): UUID("230b52e1-96b7-42eb-938a-1752f557c9c9"),
    ("catalogo", 2): UUID("4dbd9d75-183a-4231-abb2-9dc3a914d913"),
    ("catalogo", 3): UUID("135c795b-70d6-4c14-8469-14e69b9ab2f5"),
    ("catalogo", 4): UUID("6f57adc4-4fa9-4603-840c-bd35d6731e50"),
    ("catalogo", 5): UUID("86e1ba76-3e57-41ee-bd95-a0b67ce49ee0"),
}

# pricing_accounts.platform uses the long form
PLATFORM_MAP_PRICING = {
    "mercadolivre": "mercadolivre",
    "shopee": "shopee",
    "amazon": "amazon",
    "tiktok": "tiktok",
    "temu": "temu",
    "aliexpress": "aliexpress",
    "magalu": "magalu",
}

# product_links / integrations use the short form
PLATFORM_MAP_LINKS = {
    "mercadolivre": "ml",
    "shopee": "shopee",
    "amazon": "amazon",
    "tiktok": "tiktok",
    "temu": "temu",
    "bling": "bling",
}


def norm_name(s: str | None) -> str:
    """Strip platform prefixes/qualifiers and ALL whitespace so that
    SSH 'shoppe Luminin' / 'ML Aguiar 2' both reduce to 'luminin' / 'aguiar2'."""
    s = (s or "").lower()
    s = re.sub(
        r"\b(ml|shopee|shoppe|amazon|tik\s*tok|tiktok|bling|principal|geral|loja)\b",
        " ",
        s,
    )
    s = re.sub(r"\s+", "", s).strip()
    return s


def _dec(v) -> Decimal | None:
    if v is None or v == "":
        return None
    return Decimal(str(v))


def _ts(v) -> datetime | None:
    if not v:
        return None
    s = v.replace("Z", "+00:00") if v.endswith("Z") else v
    return datetime.fromisoformat(s)


def _ts_or_now(v) -> datetime:
    return _ts(v) or datetime.now(timezone.utc)


async def main() -> None:
    dump_dir = Path(os.environ.get("DUMP_DIR", "/dumps"))
    print(f"[import] dump_dir={dump_dir}")

    pas = json.load(open(dump_dir / "ssh_pricing_accounts.json"))
    pps = json.load(open(dump_dir / "ssh_pricing_products.json"))
    pos = json.load(open(dump_dir / "ssh_pricing_overrides.json"))
    pls = json.load(open(dump_dir / "ssh_product_links.json"))
    ssh_prods = json.load(open(dump_dir / "ssh_products.json"))
    ssh_integs = json.load(open(dump_dir / "ssh_integrations.json"))
    print(
        f"[import] loaded pa={len(pas)} pp={len(pps)} po={len(pos)} "
        f"pl={len(pls)} prods={len(ssh_prods)} integ={len(ssh_integs)}"
    )

    eng = create_async_engine(os.environ["DATABASE_URL"])

    async with eng.connect() as c:
        r = await c.execute(text("SELECT id, platform, name FROM davinci.integrations"))
        dv_integs = {(p, norm_name(n)): i for i, p, n in r}
        r = await c.execute(
            text("SELECT id, sku FROM davinci.products WHERE user_id = :u"),
            {"u": str(DV_USER_ID)},
        )
        dv_prods_by_sku = {sku: pid for pid, sku in r}
    print(
        f"[import] DV integrations={len(dv_integs)} products={len(dv_prods_by_sku)}"
    )

    # SSH integration_id -> DV integration UUID
    integ_map: dict[int, UUID] = {}
    unmapped_integs = []
    for si in ssh_integs:
        pl = PLATFORM_MAP_LINKS.get(si["platform"])
        if not pl:
            continue
        key = (pl, norm_name(si["name"]))
        dv_id = dv_integs.get(key)
        if dv_id:
            integ_map[si["id"]] = dv_id
        else:
            unmapped_integs.append((si["id"], si["platform"], si["name"], key[1]))
    print(f"[import] integ_map: {len(integ_map)}/{len(ssh_integs)} mapped")
    for x in unmapped_integs:
        print(f"  unmapped: ssh_id={x[0]} platform={x[1]} name='{x[2]}' (norm='{x[3]}')")

    ssh_prod_sku = {p["id"]: p["sku"] for p in ssh_prods}

    # Refuse to wipe the DB if the dump is suspiciously small — early sanity
    # check so a partial-copy dump can never trigger a destructive DELETE.
    if len(pls) < 1000 or len(pas) < 50 or len(pps) < 50:
        raise SystemExit(
            f"[abort] dump looks incomplete: pa={len(pas)} pp={len(pps)} pl={len(pls)}"
        )

    # Phase 1: build every row list in memory BEFORE touching the DB. Phase 2
    # is a single atomic transaction (DELETE + all INSERTs) so an interrupted
    # run can never leave the table in a wiped state.

    # --- INSERT pricing_accounts ---
    pa_id_map: dict[int, UUID] = {}
    pa_rows = []
    skipped_pa = 0
    for a in pas:
        dept = a["department"]
        kit = int(a["kitNumber"] or 1)
        # pricing_accounts.segment_id must be a ROOT segment so the API can
        # resolve account.department via roots_by_id lookup.
        seg = ROOT_SEGMENT_MAP.get(dept)
        if seg is None:
            skipped_pa += 1
            continue
        new_id = uuid4()
        pa_id_map[a["id"]] = new_id
        pa_rows.append(
            {
                "id": str(new_id),
                "user_id": str(DV_USER_ID),
                "name": a["name"],
                "platform": PLATFORM_MAP_PRICING.get(a["platform"], a["platform"]),
                "listing_type": a.get("listingType"),
                "segment_id": str(seg),
                "department": dept,
                "kit_number": kit,
                "commission": _dec(a.get("commission")),
                "margin1": _dec(a.get("margin1")),
                "shipping1": _dec(a.get("shipping1")),
                "margin2": _dec(a.get("margin2")),
                "shipping2": _dec(a.get("shipping2")),
                "margin3": _dec(a.get("margin3")),
                "shipping3": _dec(a.get("shipping3")),
                "margin4": _dec(a.get("margin4")),
                "shipping4": _dec(a.get("shipping4")),
                "margin5": _dec(a.get("margin5")),
                "shipping5": _dec(a.get("shipping5")),
                "server": a.get("server"),
                "email": a.get("email"),
                "phone": a.get("phone"),
                "shipping_address": a.get("shippingAddress"),
                "return_address": a.get("returnAddress"),
                "observation": a.get("observation"),
                "observation2": a.get("observation2"),
                "observation3": a.get("observation3"),
                "integration_id": str(integ_map[a["integrationId"]])
                if a.get("integrationId") and a["integrationId"] in integ_map
                else None,
                "sort_order": int(a.get("sortOrder") or 0),
                "created_at": _ts_or_now(a.get("createdAt")),
                "updated_at": _ts_or_now(a.get("updatedAt")),
            }
        )
    print(f"[insert] pricing_accounts to insert: {len(pa_rows)} (skipped: {skipped_pa})")

    async with eng.begin() as c:
        if pa_rows:
            await c.execute(
                text(
                    """
                INSERT INTO davinci.pricing_accounts
                  (id, user_id, name, platform, listing_type, segment_id, department,
                   kit_number, commission, margin1, shipping1, margin2, shipping2,
                   margin3, shipping3, margin4, shipping4, margin5, shipping5,
                   server, email, phone, shipping_address, return_address,
                   observation, observation2, observation3, integration_id,
                   sort_order, created_at, updated_at)
                VALUES
                  (:id, :user_id, :name, :platform, :listing_type, :segment_id, :department,
                   :kit_number, :commission, :margin1, :shipping1, :margin2, :shipping2,
                   :margin3, :shipping3, :margin4, :shipping4, :margin5, :shipping5,
                   :server, :email, :phone, :shipping_address, :return_address,
                   :observation, :observation2, :observation3, :integration_id,
                   :sort_order, :created_at, :updated_at)
                """
                ),
                pa_rows,
            )
    print(f"[insert] pricing_accounts: {len(pa_rows)}")

    # --- INSERT pricing_products ---
    pp_id_map: dict[int, UUID] = {}
    pp_rows = []
    skipped_pp = 0
    # Dedup by sku: keep the latest (highest id) entry per sku so
    # FK overrides still resolve to a real row.
    seen_sku: dict[str, int] = {}  # sku -> latest ssh_id seen (winner)
    pps_sorted = sorted(pps, key=lambda x: x["id"])
    for p in pps_sorted:
        sku = (p["sku"] or "").strip()
        seen_sku[sku] = p["id"]
    # Rebuild list keeping only winning rows
    winners = set(seen_sku.values())
    pps_keep = [p for p in pps if p["id"] in winners]
    # For losers, we map them later to the winner's UUID
    aliases: dict[int, int] = {}  # loser_ssh_id -> winner_ssh_id
    for p in pps:
        sku = (p["sku"] or "").strip()
        winner = seen_sku.get(sku)
        if winner and winner != p["id"]:
            aliases[p["id"]] = winner
    print(f"[dedup] pricing_products: {len(pps)} -> {len(pps_keep)} (aliased {len(aliases)} dups)")
    for p in pps_keep:
        dept = p["department"]
        pt = int(p.get("productType") or 2)
        seg = SEGMENT_MAP.get((dept, pt))
        if seg is None:
            seg = SEGMENT_MAP.get((dept, 2))
        if seg is None:
            skipped_pp += 1
            continue
        new_id = uuid4()
        pp_id_map[p["id"]] = new_id
        # Find matching DaVinci product by SKU (first comma-separated piece)
        sku = p["sku"]
        first_sku = sku.split(",")[0].strip() if sku else None
        dv_product_id = dv_prods_by_sku.get(first_sku) if first_sku else None
        ean = p.get("ean")
        if ean and len(ean) > 64:
            m = re.search(r"\b\d{13}\b", ean)
            ean = m.group(0) if m else ean[:64]
        pp_rows.append(
            {
                "id": str(new_id),
                "user_id": str(DV_USER_ID),
                "product_id": str(dv_product_id) if dv_product_id else None,
                "sku": sku,
                "name": p["name"],
                "segment_id": str(seg),
                "department": dept,
                "bling_cost_price": _dec(p.get("blingCostPrice")),
                "cost_kit1": _dec(p.get("costKit1")) or Decimal("0"),
                "cost_kit2": _dec(p.get("costKit2")),
                "cost_kit3": _dec(p.get("costKit3")),
                "cost_kit4": _dec(p.get("costKit4")),
                "description": p.get("description"),
                "model": p.get("model"),
                "ean": ean,
                "is_active": bool(p.get("isActive", 1)),
                "created_at": _ts_or_now(p.get("createdAt")),
                "updated_at": _ts_or_now(p.get("updatedAt")),
            }
        )
    print(f"[insert] pricing_products to insert: {len(pp_rows)} (skipped: {skipped_pp})")

    async with eng.begin() as c:
        # Batch in chunks of 500
        for i in range(0, len(pp_rows), 500):
            chunk = pp_rows[i : i + 500]
            await c.execute(
                text(
                    """
                INSERT INTO davinci.pricing_products
                  (id, user_id, product_id, sku, name, segment_id, department,
                   bling_cost_price, cost_kit1, cost_kit2, cost_kit3, cost_kit4,
                   description, model, ean, is_active, in_catalog,
                   created_at, updated_at)
                VALUES
                  (:id, :user_id, :product_id, :sku, :name, :segment_id, :department,
                   :bling_cost_price, :cost_kit1, :cost_kit2, :cost_kit3, :cost_kit4,
                   :description, :model, :ean, :is_active, false,
                   :created_at, :updated_at)
                """
                ),
                chunk,
            )
    print(f"[insert] pricing_products: {len(pp_rows)}")

    # --- INSERT pricing_overrides ---
    po_rows = []
    skipped_po = 0
    seen_po: set[tuple[str, str]] = set()
    for o in pos:
        ssh_pp = aliases.get(o["pricingProductId"], o["pricingProductId"])
        pp_uuid = pp_id_map.get(ssh_pp)
        pa_uuid = pa_id_map.get(o["pricingAccountId"])
        if not pp_uuid or not pa_uuid:
            skipped_po += 1
            continue
        key = (str(pp_uuid), str(pa_uuid))
        if key in seen_po:
            skipped_po += 1
            continue
        seen_po.add(key)
        cs_raw = (o.get("cellStatus") or "auto").strip()
        if cs_raw == "":
            cs_raw = "auto"
        # SSH uses lowercase 'na'/'sv'/'error'/'no_link'/'locked'/'disabled'/null
        # DaVinci enum: auto, manual, locked, disabled, NA, SV
        cs_map = {
            "na": "NA",
            "sv": "SV",
            "locked": "locked",
            "disabled": "disabled",
            "error": "auto",  # transient state, not persisted as enum
            "no_link": "auto",  # transient state
            "manual": "manual",
            "auto": "auto",
        }
        cs = cs_map.get(cs_raw.lower(), "auto")
        po_rows.append(
            {
                "id": str(uuid4()),
                "user_id": str(DV_USER_ID),
                "pricing_product_id": str(pp_uuid),
                "pricing_account_id": str(pa_uuid),
                "price_override": _dec(o.get("priceOverride")),
                "cell_status": cs,
                "created_at": _ts_or_now(o.get("createdAt")),
                "updated_at": _ts_or_now(o.get("updatedAt")),
            }
        )
    print(f"[insert] pricing_overrides to insert: {len(po_rows)} (skipped: {skipped_po})")
    async with eng.begin() as c:
        for i in range(0, len(po_rows), 1000):
            chunk = po_rows[i : i + 1000]
            await c.execute(
                text(
                    """
                INSERT INTO davinci.pricing_overrides
                  (id, user_id, pricing_product_id, pricing_account_id,
                   price_override, cell_status, created_at, updated_at)
                VALUES
                  (:id, :user_id, :pricing_product_id, :pricing_account_id,
                   :price_override, CAST(:cell_status AS cell_status),
                   :created_at, :updated_at)
                """
                ),
                chunk,
            )
    print(f"[insert] pricing_overrides: {len(po_rows)}")

    # --- INSERT product_links ---
    pl_rows = []
    skipped_pl = {"no_integ": 0, "no_product": 0, "bad_platform": 0}
    for link in pls:
        pl_pl = PLATFORM_MAP_LINKS.get(link["platform"])
        if not pl_pl:
            skipped_pl["bad_platform"] += 1
            continue
        integ_uuid = integ_map.get(link["integrationId"])
        if not integ_uuid:
            skipped_pl["no_integ"] += 1
            continue
        sku = ssh_prod_sku.get(link["productId"])
        dv_product_id = dv_prods_by_sku.get(sku) if sku else None
        if not dv_product_id:
            skipped_pl["no_product"] += 1
            continue
        sus_at = link.get("suspendedAt")
        last_err = link.get("suspendedReason")
        # Map SSH state to DaVinci link_sync_status
        # enum: ok | skipped | retryable | fatal | pending | requires_review
        if sus_at:
            sync_status = "fatal"
        else:
            sync_status = "ok" if link.get("lastSyncAt") else "pending"
        pl_rows.append(
            {
                "id": str(uuid4()),
                "user_id": str(DV_USER_ID),
                "product_id": str(dv_product_id),
                "integration_id": str(integ_uuid),
                "store_id": None,
                "platform": pl_pl,
                "external_id": link["externalId"],
                "variation_id": link.get("variationId"),
                "external_sku": None,
                "listing_title": None,
                "listing_type": link.get("listingType"),
                "stock": link.get("stock"),
                "price": None,
                "last_sync_status": sync_status,
                "last_sync_at": _ts(link.get("lastSyncAt")),
                "last_error": last_err,
                "created_at": _ts_or_now(link.get("createdAt")),
                "updated_at": _ts_or_now(link.get("updatedAt")),
            }
        )
    print(
        f"[insert] product_links to insert: {len(pl_rows)} "
        f"(skipped no_integ={skipped_pl['no_integ']} "
        f"no_product={skipped_pl['no_product']} bad_plat={skipped_pl['bad_platform']})"
    )

    async with eng.begin() as c:
        for i in range(0, len(pl_rows), 1000):
            chunk = pl_rows[i : i + 1000]
            await c.execute(
                text(
                    """
                INSERT INTO davinci.product_links
                  (id, user_id, product_id, integration_id, store_id, platform,
                   external_id, variation_id, external_sku, listing_title,
                   listing_type, stock, price, last_sync_status, last_sync_at,
                   last_error, created_at, updated_at)
                VALUES
                  (:id, :user_id, :product_id, :integration_id, :store_id,
                   CAST(:platform AS integration_platform), :external_id, :variation_id,
                   :external_sku, :listing_title, :listing_type, :stock, :price,
                   CAST(:last_sync_status AS link_sync_status),
                   :last_sync_at,
                   :last_error,
                   :created_at, :updated_at)
                ON CONFLICT (user_id, platform, integration_id, external_id, COALESCE(variation_id, ''::text)) DO NOTHING
                """
                ),
                chunk,
            )
    print(f"[insert] product_links: {len(pl_rows)}")

    # Final verification
    async with eng.connect() as c:
        for t in [
            "pricing_accounts",
            "pricing_products",
            "pricing_overrides",
            "product_links",
        ]:
            r = await c.execute(
                text(
                    f"SELECT count(*) FROM davinci.{t} WHERE user_id = :u"
                ),
                {"u": str(DV_USER_ID)},
            )
            print(f"  {t}: {r.scalar()}")

    await eng.dispose()
    print("[import] done")


if __name__ == "__main__":
    asyncio.run(main())
