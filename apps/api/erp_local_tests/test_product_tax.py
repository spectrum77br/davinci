import json

import pytest
from test_core import run
from test_sales import body, doc

from app.erp_local import product_tax, release
from app.erp_local.core import ERPError


def save(erp, data, version=0):
    return run(
        erp,
        "product.tax.save",
        {
            "sku": "demo.aparelho",
            "version": version,
            "data": data,
            "reason": "Cadastro fiscal de teste isolado",
        },
    )


def test_unspecified_tax_registration_never_infers_national_origin_or_zero_taxes(erp):
    with erp.connection() as db:
        empty = product_tax.details(db, "demo.aparelho")
    assert empty["version"] == 0 and empty["data"]["origin"] is None
    assert empty["data"]["approximate_tax_percent"] is None
    before = erp.snapshot()
    result = save(
        erp,
        {
            "ncm": "0000.00.00",
            "cest": "01.002.03",
            "origin": "2",
            "item_type": "00",
            "pis_fixed_value": "0",
            "tipi_exception": "001",
        },
    )
    assert result["classification_verified"] is False and result["external_writes"] is False
    product = next(p for p in erp.snapshot()["products"] if p["sku"] == "demo.aparelho")
    assert product["tax"]["data"]["ncm"] == "00000000"
    assert product["tax"]["data"]["cest"] == "0100203"
    assert product["tax"]["data"]["tipi_exception"] == "001"
    assert product["tax"]["data"]["pis_fixed_value"] == "0.0000"
    assert (
        erp.snapshot()["movements"] == before["movements"]
        and erp.snapshot()["accounts"] == before["accounts"]
    )


@pytest.mark.parametrize(
    "data",
    [
        {"ncm": 12345678},
        {"ncm": "1234567"},
        {"cest": "12345678"},
        {"origin": 0},
        {"origin": "9"},
        {"item_type": "11"},
        {"approximate_tax_percent": "100.0001"},
        {"icms_st_retention_value": "0.00001"},
        {"additional_info": "X" * 501},
    ],
)
def test_invalid_tax_registration_is_rejected_without_partial_metadata_or_history(erp, data):
    before = erp.snapshot()
    with pytest.raises(ERPError):
        save(erp, data)
    assert erp.snapshot() == before


def test_tax_reference_is_frozen_on_order_and_later_catalogue_changes_do_not_rewrite_it(erp):
    save(erp, {"origin": "2", "ncm": "00000000", "additional_info": "Dado fiscal de demonstração"})
    identifier = run(
        erp,
        "order.save",
        body(items=[{"sku": "demo.aparelho", "quantity": "1", "unit_price": "10"}]),
    )["id"]
    save(
        erp,
        {"origin": "0", "ncm": "99999999", "additional_info": "Outra versão fictícia"},
        version=1,
    )
    frozen = doc(erp, identifier)["data"]["product_snapshots"]["demo.aparelho"]["tax"]
    assert frozen["version"] == 1 and frozen["data"]["origin"] == "2"
    assert frozen["data"]["ncm"] == "00000000"
    assert frozen["data"]["additional_info"] == "Dado fiscal de demonstração"
    assert not erp.snapshot()["accounts"]


def test_stale_tax_version_rolls_back_product_name_and_supplier_changes_together(erp):
    save(erp, {"origin": "2"})
    before = erp.snapshot()
    with pytest.raises(ERPError, match="cadastro fiscal mudou"):
        run(
            erp,
            "product.update",
            {
                "sku": "demo.aparelho",
                "version": 0,
                "data": {"name": "Não deve persistir"},
                "suppliers": {"version": 0, "rows": [], "reason": "Alteração conjunta"},
                "tax": {"version": 0, "data": {"origin": "0"}, "reason": "Versão obsoleta"},
            },
        )
    assert erp.snapshot() == before


def test_tax_history_is_preserved_in_backup_and_detects_tampering(erp, tmp_path):
    save(erp, {"origin": "2", "ncm": "00000000"})
    save(erp, {"ncm": "99999999"}, version=1)
    backup = tmp_path / "tax-copy.sqlite3"
    release.backup(erp.database, backup)
    with release.connect(backup) as db:
        history = product_tax.history(db, "demo.aparelho")
        assert [r["version"] for r in history] == [2, 1]
    assert release.inspect_database(backup)["ledger_ok"]
    with erp.connection() as db:
        db.execute(
            "UPDATE product_tax SET data=? WHERE sku='demo.aparelho'",
            (json.dumps({"origin": "8"}),),
        )
    assert "product_tax_history_mismatch" in release.inspect_database(erp.database)["issues"]
