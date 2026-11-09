import pytest
from test_core import order, run

from app.erp_local import categories
from app.erp_local.core import ERPError


def save(erp, name, **fields):
    return run(erp, "category.save", {"data": {"name": name, **fields}})["id"]


def test_hierarchy_and_assignment_do_not_move_stock_and_orders_freeze_path(erp):
    parent = save(erp, "Eletrônicos")
    child = save(erp, "Aparelhos", parent_id=parent)
    before = erp.snapshot()["movements"]
    run(
        erp,
        "category.assign",
        {"items": [{"sku": "demo.aparelho", "category_id": child, "version": 0}]},
    )
    created = order(erp, sku="demo.aparelho")["id"]
    frozen = next(o for o in erp.snapshot()["orders"] if o["id"] == created)["data"][
        "product_snapshots"
    ]["demo.aparelho"]["category"]
    assert [p["name"] for p in frozen["path"]] == ["Eletrônicos", "Aparelhos"]
    run(erp, "category.save", {"id": parent, "version": 1, "data": {"name": "Linha atualizada"}})
    with erp.connection() as db:
        assert (
            categories.document_snapshot(db, "demo.aparelho")["path"][0]["name"]
            == "Linha atualizada"
        )
    existing = next(o for o in erp.snapshot()["orders"] if o["id"] == created)
    assert existing["data"]["product_snapshots"]["demo.aparelho"]["category"] == frozen
    assert (
        len(erp.snapshot()["movements"]) == len(before) + 1
    )  # Only the explicit sales reservation.


def test_cycle_duplicate_and_stale_edits_are_rejected(erp):
    parent = save(erp, "Áudio")
    child = save(erp, "Fones", parent_id=parent)
    with pytest.raises(ERPError, match="descendente"):
        run(
            erp,
            "category.save",
            {"id": parent, "version": 1, "data": {"name": "Áudio", "parent_id": child}},
        )
    with pytest.raises(ERPError, match="este nome"):
        save(erp, "ÁUDIO")
    with pytest.raises(ERPError, match="mudou"):
        run(erp, "category.save", {"id": parent, "version": 0, "data": {"name": "X"}})


def test_batch_conflict_rolls_back_every_assignment(erp):
    category = save(erp, "Local")
    before = erp.snapshot()
    with pytest.raises(ERPError, match="mudou"):
        run(
            erp,
            "category.assign",
            {
                "items": [
                    {"sku": "demo.aparelho", "category_id": category, "version": 0},
                    {"sku": "demo.fone", "category_id": category, "version": 1},
                ]
            },
        )
    assert erp.snapshot() == before


def test_inactive_ancestor_blocks_new_links_but_preserves_current_category(erp):
    parent = save(erp, "Raiz")
    child = save(erp, "Filha", parent_id=parent)
    run(
        erp,
        "category.assign",
        {"items": [{"sku": "demo.aparelho", "category_id": child, "version": 0}]},
    )
    run(
        erp,
        "category.save",
        {"id": parent, "version": 1, "data": {"name": "Raiz", "status": "inactive"}},
    )
    with pytest.raises(ERPError, match="inativa"):
        run(
            erp,
            "category.assign",
            {"items": [{"sku": "demo.fone", "category_id": child, "version": 0}]},
        )
    with erp.connection() as db:
        assert categories.document_snapshot(db, "demo.aparelho")["id"] == child
        assert not next(c for c in categories.snapshot(db) if c["id"] == child)["effective_active"]
    run(
        erp,
        "category.assign",
        {"items": [{"sku": "demo.aparelho", "category_id": None, "version": 1}]},
    )


def test_store_mapping_requires_exact_identified_account_and_never_publishes(erp):
    with pytest.raises(ERPError, match="conta identificada"):
        save(
            erp, "Local", links=[{"channel": "ml", "store": "similar-name", "external_id": "MLB1"}]
        )
