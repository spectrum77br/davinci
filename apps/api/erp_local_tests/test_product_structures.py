import json
from decimal import Decimal

import pytest
from test_core import balance, order, run
from test_product_details import update
from test_production import state as production_state

from app.erp_local.core import ERPError
from app.erp_local.release import inspect_database


def product(erp, sku="demo.kit"):
    return next(p for p in erp.snapshot()["products"] if p["sku"] == sku)


def save(erp, sku="demo.kit", parts=None, **extra):
    return run(
        erp,
        "product.structure.save",
        {
            "sku": sku,
            "version": product(erp, sku)["structure"]["version"],
            "reason": "Receita própria validada",
            "components": parts
            or [
                {"sku": "demo.aparelho", "quantity": "1"},
                {"sku": "demo.fone", "quantity": "2"},
            ],
            **extra,
        },
    )


def test_structure_edit_preserves_saved_reservations_requirements_and_sale_snapshot(erp):
    old = order(erp)["id"]
    before = erp.snapshot()["movements"]
    save(erp)
    assert erp.snapshot()["movements"] == before
    assert balance(erp, "demo.fone") == (5, 1, 4)
    frozen = next(o for o in erp.snapshot()["orders"] if o["id"] == old)["data"]
    assert frozen["requirements"] == {"demo.aparelho": "1.000", "demo.fone": "1.000"}
    assert frozen["product_snapshots"]["demo.kit"]["structure"]["version"] == 0
    new = order(erp, external="NOVA-COMPOSICAO")["id"]
    assert balance(erp, "demo.fone") == (5, 3, 2)
    run(erp, "order.cancel", {"id": old})
    assert balance(erp, "demo.fone") == (5, 2, 3)
    run(erp, "order.ship", {"id": new})
    assert balance(erp, "demo.fone") == (3, 0, 3)
    assert inspect_database(erp.database)["ledger_ok"]


def test_nested_kits_update_all_ancestors_but_never_rewrite_saved_orders(erp):
    run(
        erp,
        "product.create",
        {
            "sku": "parent.kit",
            "name": "Kit aninhado",
            "kind": "kit",
            "components": [
                {"sku": "demo.kit", "quantity": "2"},
                {"sku": "demo.aparelho", "quantity": "1"},
            ],
        },
    )
    old = order(erp, sku="parent.kit")["id"]
    result = save(
        erp,
        parts=[{"sku": "demo.aparelho", "quantity": "2"}, {"sku": "demo.fone", "quantity": "1"}],
    )
    assert result["affected"] == ["demo.kit", "parent.kit"]
    parent = product(erp, "parent.kit")
    assert parent["structure"]["version"] == 2
    assert parent["components"] == [
        {"sku": "demo.aparelho", "quantity": "5.000"},
        {"sku": "demo.fone", "quantity": "2.000"},
    ]
    frozen = next(o for o in erp.snapshot()["orders"] if o["id"] == old)["data"]
    assert {s: Decimal(q) for s, q in frozen["requirements"].items()} == {
        "demo.aparelho": Decimal(3),
        "demo.fone": Decimal(2),
    }
    assert [h["version"] for h in parent["structure"]["history"]] == [2, 1, 0]
    before = erp.snapshot()
    with pytest.raises(ERPError, match="ciclo"):
        save(erp, parts=[{"sku": "parent.kit", "quantity": "1"}])
    assert erp.snapshot() == before


def test_product_metadata_and_recipe_save_atomically_with_independent_versions(erp):
    run(
        erp,
        "product.update",
        {
            "sku": "demo.kit",
            "version": 0,
            "data": {"name": "Nome novo"},
            "structure": {
                "version": 0,
                "reason": "Nova estrutura",
                "components": [{"sku": "demo.fone", "quantity": "2"}],
            },
        },
    )
    assert product(erp)["name"] == "Nome novo"
    assert product(erp)["details"]["version"] == 1
    before = erp.snapshot()
    with pytest.raises(ERPError, match="estrutura mudou"):
        run(
            erp,
            "product.update",
            {
                "sku": "demo.kit",
                "version": 1,
                "data": {"name": "Perder"},
                "structure": {
                    "version": 0,
                    "reason": "Obsoleta",
                    "components": [{"sku": "demo.aparelho", "quantity": "2"}],
                },
            },
        )
    assert erp.snapshot() == before


def test_physical_recipe_is_a_master_not_a_stock_posting_and_production_freezes_it(erp):
    run(erp, "product.create", {"sku": "physical.recipe", "name": "Produto montado"})
    parts = [{"sku": "demo.kit", "quantity": "1"}, {"sku": "demo.aparelho", "quantity": "1"}]
    save(erp, "physical.recipe", parts)
    physical = product(erp, "physical.recipe")
    assert physical["kind"] == "simple" and physical["components"] == []
    assert balance(erp, "physical.recipe") == (0, 0, 0)
    frozen = physical["structure"]
    payload = {
        "reference": "OP-RECEITA",
        "sku": "physical.recipe",
        "quantity": "2",
        "origin": "geral",
        "destination": "geral",
        "structure_version": frozen["version"],
        "components": frozen["expanded"],
    }
    oid = run(erp, "production.save", payload)["id"]
    save(erp, "physical.recipe", [{"sku": "demo.aparelho", "quantity": "1"}])
    with pytest.raises(ERPError, match="estrutura mudou"):
        run(erp, "production.save", {**payload, "reference": "OP-OBSOLETA"})
    run(erp, "production.start", {"id": oid})
    assert balance(erp) == (8, 4, 4)
    assert balance(erp, "demo.fone") == (5, 2, 3)
    own = production_state(erp)["orders"][0]
    assert own["data"]["structure_snapshot"]["version"] == 1
    assert own["data"]["components"] == frozen["expanded"]
    assert not erp.snapshot()["accounts"]


def test_invalid_inherited_precision_and_inactive_components_rollback_the_graph(erp):
    save(erp, parts=[{"sku": "demo.fone", "quantity": "0.001"}])
    before = erp.snapshot()
    with pytest.raises(ERPError, match="casas"):
        run(
            erp,
            "product.create",
            {
                "sku": "too.precise",
                "name": "Inválido",
                "kind": "kit",
                "components": [{"sku": "demo.kit", "quantity": "0.001"}],
            },
        )
    assert erp.snapshot() == before
    update(erp, sku="demo.fone", status="inactive")
    with pytest.raises(ERPError, match="inativo"):
        save(erp, parts=[{"sku": "demo.fone", "quantity": "1"}])


def test_inspection_detects_structure_history_or_expansion_divergence(erp):
    save(erp)
    assert inspect_database(erp.database)["ledger_ok"]
    with erp.connection() as db:
        db.execute(
            "UPDATE products SET components=? WHERE sku='demo.kit'",
            (json.dumps([{"sku": "demo.fone", "quantity": "1.000"}]),),
        )
    assert "product_structure_expansion_mismatch" in inspect_database(erp.database)["issues"]


def test_structure_change_updates_unsent_channel_preview_without_posting_stock(erp):
    from test_channels import routes

    from app.erp_local.channels import ChannelFlow

    routes(erp)
    before = erp.snapshot()["movements"]
    save(erp)
    state = ChannelFlow(erp).snapshot()
    assert [(r["sku"], r["quantity"], r["status"]) for r in state["intents"]] == [
        ("demo.kit", "2", "preview")
    ]
    assert state["external_writes"] is False
    assert erp.snapshot()["movements"] == before
