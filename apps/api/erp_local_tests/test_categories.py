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


def test_usage_counts_category_names_and_sku_endings_without_deleted_products(erp):
    """M-29: DaVinci's Cadastros › Produtos asks this before a category or a shipping tag leaves
    its official list. Deleted products are not counted (the list does not show them)."""
    from test_product_details import batch_items

    celular, mala = save(erp, "Celular"), save(erp, "Mala")
    for sku in ("dg105.sp", "dg105.sp+a001.sp", "dg106.PI", "a001.pi", "velho.sp"):
        run(erp, "product.create", {"sku": sku, "name": sku})
    run(
        erp,
        "category.assign",
        {
            "items": [
                {"sku": "dg105.sp", "category_id": celular, "version": 0},
                {"sku": "dg105.sp+a001.sp", "category_id": celular, "version": 0},
                {"sku": "a001.pi", "category_id": mala, "version": 0},
                {"sku": "velho.sp", "category_id": mala, "version": 0},
            ]
        },
    )
    run(erp, "product.delete", {"items": batch_items(erp, "velho.sp")})
    with erp.connection() as db:
        seen = categories.usage(db, [".sp", ".pi", ".ra", ".sp"])
    # 3 demo products + 4 new ones still listed (velho.sp is deleted).
    assert seen["products"] == 7
    assert seen["categories"] == {"Celular": 2, "Mala": 1}
    assert seen["without_category"] == 4
    assert seen["tags"] == {".sp": 2, ".pi": 2, ".ra": 0}


def test_usage_route_answers_davinci_behind_the_gate_read_only(tmp_path, monkeypatch):
    """DaVinci's API calls http://erp:8765 inside davinci_net as a plain user: a read is allowed,
    the same headers never post."""
    from fastapi.testclient import TestClient

    from app.erp_local.api import create_app

    monkeypatch.setenv("ERP_TRUST_PROXY", "1")
    monkeypatch.setenv("ERP_ALLOWED_HOSTS", "erp")
    with TestClient(create_app(tmp_path / "api.sqlite3"), base_url="http://erp:8765") as c:
        run(c.app.state.erp, "demo.seed", {})
        who = {"x-erp-actor": "davinci-api/cadastro-produtos", "x-erp-role": "user"}
        assert c.get("/api/erp-local/catalog/usage").status_code == 403
        reply = c.get("/api/erp-local/catalog/usage", params={"tag": ".sp"}, headers=who)
        assert reply.status_code == 200, reply.text
        assert reply.json() == {
            "products": 3,
            "categories": {},
            "without_category": 3,
            "tags": {".sp": 0},
        }
        posted = c.post(
            "/api/erp-local/commands",
            headers={**who, "x-erp-local": "1"},
            json={"operation_key": "k", "kind": "category.save", "payload": {}},
        )
        assert posted.status_code == 403
