import pytest

from app.erp_local.native_reconciliation import compare
from app.erp_local.stock_families import key


def test_family_identity_keeps_all_kit_components_and_excludes_distribution_used_and_mixed_lots():
    assert key("dg053.ci+a001.ci") == key("dg053.sp+a001.sp") == "dg053+a001"
    assert key("dg053.ci+a003.ci") != key("dg053.sp+a001.sp")
    assert key("dg053.ci") != key("dg053.ci+a001.ci")
    assert key("dg053.cd") is None and key("dg053.us") is None
    assert key("dg053.ci+a001.sp") is None


@pytest.mark.parametrize(
    "sku", ["b009.8.12.20.24", "uaf001m1.110", "fake.dg053.ci", "z0393", "unknown", ""]
)
def test_sizes_voltages_salvaged_codes_and_unknown_suffixes_do_not_create_sale_families(sku):
    assert key(sku) is None


def test_migration_recognizes_lot_change_without_replacing_the_order_or_its_reservations():
    order = {"id": "O1", "items": [{"line_id": "L1", "quantity": None}]}
    local = [{"line_id": "L1", "sku": "dg053.ci", "state": "matched"}]
    source = {
        "captured_at": "2026-11-05T20:00:00Z",
        "policies": {("tiktok", "ACCOUNT"): "STORE"},
        "orders": {
            ("STORE", "O1"): [{"number": "300", "items": [{"sku": "dg053.sp", "quantity": "1"}]}]
        },
    }
    result = compare("tiktok", "ACCOUNT", order, local, source)
    assert result["state"] == "historical_lot_change" and result["source_skus"] == ["dg053.sp"]
    assert not result["operational_match"] and order["items"][0]["quantity"] is None
    assert local[0]["sku"] == "dg053.ci"
