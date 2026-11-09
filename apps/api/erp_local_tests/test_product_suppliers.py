import copy

import pytest
from test_contacts import save as contact
from test_core import run
from test_purchase_documents import purchase

from app.erp_local import product_suppliers, release
from app.erp_local.core import ERPError


def row(identifier, **changes):
    return {
        "contact_id": identifier,
        "description": "Descrição do fornecedor",
        "code": "000-ABC",
        "purchase_price": "9.125",
        "cost_price": "10.00",
        "warranty_months": 12,
        "is_default": True,
        **changes,
    }


def save(erp, rows, version=0):
    return run(
        erp,
        "product.suppliers.save",
        {
            "sku": "demo.aparelho",
            "version": version,
            "rows": rows,
            "reason": "Conferência dos fornecedores",
        },
    )


def test_supplier_references_are_versioned_without_changing_stock_valuation_or_catalogue_cost(erp):
    supplier = contact(erp, roles=["supplier"])
    before = erp.snapshot()
    assert save(erp, [row(supplier["id"])])["supplier_version"] == 1
    after = erp.snapshot()
    assert after["movements"] == before["movements"] and after["accounts"] == before["accounts"]
    old = next(p for p in before["products"] if p["sku"] == "demo.aparelho")
    product = next(p for p in after["products"] if p["sku"] == "demo.aparelho")
    assert product["cost_price"] == old["cost_price"] and product["balances"] == old["balances"]
    assert product["suppliers"]["rows"][0]["purchase_price"] == "9.1250"
    assert product["suppliers"]["rows"][0]["cost_price"] == "10.0000"
    assert product["suppliers"]["rows"][0]["code"] == "000-ABC"
    assert release.inspect_database(erp.database)["ledger_ok"]


@pytest.mark.parametrize("kind", ["duplicate", "none_default", "two_defaults", "warranty", "role"])
def test_supplier_list_guards_duplicate_default_warranty_and_contact_role_atomically(erp, kind):
    first = contact(erp, roles=["supplier"])
    second = contact(
        erp, name="Outro contato", roles=["customer"] if kind == "role" else ["supplier"]
    )
    rows = [row(first["id"]), row(second["id"], is_default=False)]
    if kind == "duplicate":
        rows[1]["contact_id"] = first["id"]
    elif kind == "none_default":
        rows[0]["is_default"] = False
    elif kind == "two_defaults":
        rows[1]["is_default"] = True
    elif kind == "warranty":
        rows[0]["warranty_months"] = True
    before = erp.snapshot()
    with pytest.raises(ERPError):
        save(erp, rows)
    assert erp.snapshot() == before


def test_supplier_version_conflicts_and_product_metadata_save_are_one_transaction(erp):
    supplier = contact(erp, roles=["supplier"])
    save(erp, [row(supplier["id"])])
    before = erp.snapshot()
    with pytest.raises(ERPError, match="mudaram"):
        save(erp, [row(supplier["id"], code="NEW")], version=0)
    with pytest.raises(ERPError, match="padrão"):
        run(
            erp,
            "product.update",
            {
                "sku": "demo.aparelho",
                "version": 0,
                "data": {"name": "Nome que deve reverter"},
                "suppliers": {
                    "version": 1,
                    "rows": [row(supplier["id"], is_default=False)],
                    "reason": "Erro no padrão",
                },
            },
        )
    assert erp.snapshot() == before


def test_purchase_freezes_supplier_code_prices_and_warranty_until_explicit_refresh(erp):
    supplier = contact(erp, roles=["supplier"])
    save(erp, [row(supplier["id"])])
    result, payload = purchase(erp, contact_id=supplier["id"])
    identifier = result["id"]
    original = copy.deepcopy(erp.snapshot()["purchases"][0]["data"])
    save(erp, [row(supplier["id"], code="NEW", purchase_price="12", warranty_months=24)], version=1)
    run(
        erp,
        "purchase.update",
        {**payload, "id": identifier, "version": 1, "reason": "Alterar previsão"},
    )
    got = erp.snapshot()["purchases"][0]["data"]
    assert got["supplier_items"] == original["supplier_items"]
    assert got["items"] == original["items"] and got["landed_totals"] == original["landed_totals"]
    run(
        erp,
        "purchase.update",
        {
            **payload,
            "id": identifier,
            "version": 2,
            "reason": "Conferir referência atual",
            "refresh_supplier_items": True,
        },
    )
    refreshed = erp.snapshot()["purchases"][0]["data"]
    assert refreshed["supplier_items"]["demo.aparelho"]["code"] == "NEW"
    assert refreshed["supplier_items"]["demo.aparelho"]["warranty_months"] == 24
    assert refreshed["items"] == original["items"]
    assert not erp.snapshot()["accounts"]


def test_removing_suppliers_preserves_old_purchases_and_history_and_unknown_prices(erp, tmp_path):
    supplier = contact(erp, roles=["supplier"])
    save(erp, [row(supplier["id"], purchase_price=None, cost_price="0")])
    result, payload = purchase(erp, contact_id=supplier["id"])
    assert (
        erp.snapshot()["purchases"][0]["data"]["supplier_items"]["demo.aparelho"]["purchase_price"]
        is None
    )
    save(erp, [], version=1)
    run(
        erp,
        "purchase.update",
        {**payload, "id": result["id"], "version": 1, "reason": "Histórico preservado"},
    )
    assert (
        erp.snapshot()["purchases"][0]["data"]["supplier_items"]["demo.aparelho"]["code"]
        == "000-ABC"
    )
    run(
        erp,
        "purchase.update",
        {
            **payload,
            "id": result["id"],
            "version": 2,
            "reason": "Retirar vínculo",
            "refresh_supplier_items": True,
        },
    )
    assert "supplier_items" not in erp.snapshot()["purchases"][0]["data"]
    backup = tmp_path / "supplier-copy.sqlite3"
    release.backup(erp.database, backup)
    with release.connect(backup) as db:
        assert len(product_suppliers.history(db, "demo.aparelho")) == 2
    assert release.inspect_database(backup)["ledger_ok"]
