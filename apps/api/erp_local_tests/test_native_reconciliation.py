import copy

from app.erp_local.native_reconciliation import compare


def inputs(channel="shopee", quantity=2):
    order = {"id": "ORDER-ID", "items": [{"line_id": "LINE", "quantity": quantity}]}
    items = [{"line_id": "LINE", "sku": "demo.kit", "state": "matched"}]
    source = {
        "captured_at": "2026-11-05T20:00:00Z",
        "policies": {(channel, "ACCOUNT"): "BLING-ID"},
        "orders": {
            ("BLING-ID", "ORDER-ID"): [
                {"number": "200", "items": [{"sku": "demo.kit", "quantity": "2"}]}
            ]
        },
    }
    return order, items, source


def test_comparison_uses_exact_store_and_order_ids_and_never_enables_processing():
    order, items, source = inputs()
    before = copy.deepcopy(source)
    result = compare("shopee", "ACCOUNT", order, items, source)
    assert result["state"] == "items_match" and not result["operational_match"]
    assert result["source_numbers"] == ["200"] and source == before
    assert (
        compare("shopee", "OTHER-ACCOUNT", order, items, source)["state"] == "store_identity_review"
    )
    order["id"] = "OTHER-ORDER"
    assert compare("shopee", "ACCOUNT", order, items, source)["state"] == "source_not_found"


def test_current_route_cannot_replace_a_different_historical_source_sku():
    order, items, source = inputs()
    items[0]["sku"] = "different.depot"
    result = compare("shopee", "ACCOUNT", order, items, source)
    assert result["state"] == "sku_difference" and result["source_skus"] == ["demo.kit"]
    assert items[0]["sku"] == "different.depot" and not result["operational_match"]


def test_tiktok_line_count_comparison_does_not_infer_quantity_even_when_it_matches():
    order, items, source = inputs("tiktok", None)
    source["orders"][("BLING-ID", "ORDER-ID")][0]["items"][0]["quantity"] = "1"
    result = compare("tiktok", "ACCOUNT", order, items, source)
    assert result["state"] == "quantity_review" and result["unit_line_count_matches"] is True
    assert order["items"][0]["quantity"] is None and not result["operational_match"]
    source["orders"][("BLING-ID", "ORDER-ID")][0]["items"][0]["quantity"] = "2"
    assert compare("tiktok", "ACCOUNT", order, items, source)["unit_line_count_matches"] is False


def test_ambiguous_source_missing_identity_and_special_composition_remain_explicit():
    order, items, source = inputs()
    source["orders"][("BLING-ID", "ORDER-ID")] *= 2
    assert compare("shopee", "ACCOUNT", order, items, source)["state"] == "source_ambiguous"
    source["orders"][("BLING-ID", "ORDER-ID")] = source["orders"][("BLING-ID", "ORDER-ID")][:1]
    items[0]["state"] = "missing"
    assert compare("shopee", "ACCOUNT", order, items, source)["state"] == "listing_identity_review"
    items[0]["state"] = "matched"
    order["items"][0]["composition_review"] = True
    assert compare("shopee", "ACCOUNT", order, items, source)["state"] == "composition_review"
    assert compare("shopee", "ACCOUNT", order, items, None)["state"] == "source_unavailable"
