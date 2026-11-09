"""Compare migration observations by store/order IDs, never by customer or name.

The current listing route is not necessarily the SKU frozen on a historical
Bling order. Differences are evidence for reconciliation, not route edits.
"""

import json
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation

from app.erp_local.stock_families import key as family_key


def context(db):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"reference_rows", "reference_meta"} <= tables:
        return None
    meta = db.execute("SELECT data FROM reference_meta WHERE id=1").fetchone()
    if not meta:
        return None
    policies = {}
    for row in db.execute("SELECT channel,store,status,data FROM store_policies"):
        data = json.loads(row["data"])
        if row["status"] == "shadow" and data.get("source_profile", {}).get("bling_store_id"):
            policies[(row["channel"], row["store"])] = str(data["source_profile"]["bling_store_id"])
    orders = defaultdict(list)
    for row in db.execute("SELECT data FROM reference_rows WHERE kind='orders'"):
        data = json.loads(row[0])
        orders[(str(data["store"]), data["store_number"])].append(data)
    return {
        "policies": policies,
        "orders": orders,
        "captured_at": json.loads(meta[0])["captured_at"],
    }


def compare(channel, store, order, local_items, source):
    result = {
        "state": "source_unavailable",
        "source_numbers": [],
        "source_skus": [],
        "reference_captured_at": source["captured_at"] if source else None,
        "unit_line_count_matches": None,
        "operational_match": False,
    }
    if not source:
        return result
    bling_store = source["policies"].get((channel, store))
    if not bling_store:
        return {**result, "state": "store_identity_review"}
    candidates = source["orders"].get((bling_store, order["id"]), [])
    if not candidates and channel == "ml" and order.get("pack_id"):
        packed = source["orders"].get((bling_store, order["pack_id"]), [])
        if packed:
            # One source document may represent several ML orders in a cart.
            # Never compare one member's quantity to the whole source document.
            return {
                **result,
                "state": "group_identity_review",
                "source_numbers": [c["number"] for c in packed[:10]],
            }
    if len(candidates) != 1:
        return {
            **result,
            "state": "source_ambiguous" if candidates else "source_not_found",
            "source_numbers": [c["number"] for c in candidates[:10]],
        }
    original = candidates[0]
    result["source_numbers"] = [original["number"]]
    try:
        expected = defaultdict(Decimal)
        for item in original["items"]:
            quantity = Decimal(str(item["quantity"]))
            if not quantity.is_finite() or quantity < 0 or not item["sku"]:
                raise ValueError
            expected[item["sku"]] += quantity
        if not expected:
            raise ValueError
    except (ValueError, KeyError, TypeError, InvalidOperation):
        return {**result, "state": "source_items_review"}
    result["source_skus"] = sorted(expected)
    mapped = {i["line_id"]: i for i in local_items}
    if not order["items"] or any(
        mapped.get(i["line_id"], {}).get("state") != "matched" for i in order["items"]
    ):
        return {**result, "state": "listing_identity_review"}
    if any(i.get("composition_review") for i in order["items"]):
        return {**result, "state": "composition_review"}
    if any(i.get("measurement_review") for i in order["items"]):
        return {**result, "state": "measurement_review"}
    skus = {mapped[i["line_id"]]["sku"] for i in order["items"]}
    if skus != set(expected):
        current_families, original_families = (
            {family_key(s) for s in skus},
            {family_key(s) for s in expected},
        )
        if (
            None not in current_families | original_families
            and current_families == original_families
        ):
            # Same product family does not merge stock, infer quantities or
            # authorize changing the lot already reserved on the old order.
            return {**result, "state": "historical_lot_change"}
        return {**result, "state": "sku_difference"}
    if any(i["quantity"] is None for i in order["items"]):
        if channel == "tiktok" and all(i["quantity"] is None for i in order["items"]):
            counts = Counter(mapped[i["line_id"]]["sku"] for i in order["items"])
            result["unit_line_count_matches"] = dict(counts) == dict(expected)
        return {**result, "state": "quantity_review"}
    actual = defaultdict(Decimal)
    for item in order["items"]:
        actual[mapped[item["line_id"]]["sku"]] += Decimal(item["quantity"])
    if actual != expected:
        return {**result, "state": "quantity_difference"}
    # This is an item comparison only. Status, payment, opening reservations,
    # historical changes and authority still need their own cutover checks.
    return {**result, "state": "items_match", "operational_match": False}
