"""DaVinci's real lot priorities, read the way its robot reads them."""

from app.erp_local import stock_priority as sp


def test_map_reads_comma_lists_any_lot_and_drops_conflicts():
    rows = [
        {"sku": "dg053.ci, dg054", "priority": "sp"},
        {"sku": "i203,i204", "priority": "ra"},
        {"sku": "x001.sp+a001.sp", "priority": "ci"},
        {"sku": "i204", "priority": "sp"},
        {"sku": "a009", "priority": ""},
    ]
    assert sp.map_from_rows(rows) == {"dg053": "sp", "dg054": "sp", "i203": "ra", "x001": "ci"}


def test_preferred_moves_whole_kit_only_when_target_covers():
    mapping = {"dg053": "sp"}
    assert sp.preferred("dg053.ci+a001.ci", mapping) == "dg053.sp+a001.sp"
    assert sp.preferred("dg053.ci", mapping, {"dg053.sp": "3"}, 2) == "dg053.sp"
    assert sp.preferred("dg053.ci", mapping, {"dg053.sp": "1"}, 2) == "dg053.ci"
    assert sp.preferred("dg053.ci", mapping, {}, 1) == "dg053.ci"
    assert sp.preferred("dg053.sp", mapping) == "dg053.sp"
    assert sp.preferred("b009.8", mapping) == "b009.8"


def test_sync_keeps_rules_equal_to_the_table(erp):
    assert sp.sync_rules(erp, [{"sku": "dg053", "priority": "sp"}]) == {"rules": 1, "changed": 1}
    assert sp.sync_rules(erp, [{"sku": "dg053", "priority": "sp"}]) == {"rules": 1, "changed": 0}
    assert sp.sync_rules(erp, []) == {"rules": 0, "changed": 1}
    with erp.connection() as db:
        assert db.execute("SELECT count(*) FROM stock_priority_rules").fetchone()[0] == 0


def test_whole_sale_follows_flex_then_one_lot_then_priority():
    from app.erp_local.stock_priority import preferred_order

    available = {"dg1.sp": 5, "dg1.ci": 3, "a1.sp": 5, "a1.ci": 1, "dg1.pi": 2, "a1.pi": 2}
    # Flex leaves from the Flex lot when it covers; otherwise it stays for a person.
    assert preferred_order([("dg1.ci", 1)], {}, available, flex=True) == ["dg1.sp"]
    assert preferred_order([("dg1.ci", 6)], {}, available, flex=True) == ["dg1.ci"]
    # Two items stay in one lot: the priority voted for, when it covers both.
    assert preferred_order([("dg1.ci", 1), ("a1.sp", 1)], {"dg1": "pi"}, available) == [
        "dg1.pi",
        "a1.pi",
    ]
    # No priority: the lot with more balance among those covering both.
    assert preferred_order([("dg1.ci", 1), ("a1.sp", 1)], {}, available) == ["dg1.sp", "a1.sp"]
    # Already in one lot that covers: nothing moves.
    assert preferred_order([("dg1.ci", 1), ("a1.ci", 1)], {}, available) == ["dg1.ci", "a1.ci"]
    # One item: its own priority, guarded by the balance.
    assert preferred_order([("dg1.ci", 1)], {"dg1": "sp"}, available) == ["dg1.sp"]
