from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.erp_local.core import ERPError, LocalERP
from app.erp_local.initial_load import bootstrap_real_catalog
from app.erp_local.reference import FIELDS, ReferenceStore, SSHReference

STAMP = "2026-11-05T14:00:00+00:00"


def product(identifier, sku, kind="S", physical=8, available=6, status="A"):
    row = dict.fromkeys(FIELDS["products"])
    row.update(
        id=identifier,
        bling_product_id=identifier,
        sku=sku,
        name=sku,
        formato=kind,
        situacao=status,
        stock=available,
        reserved_stock=2,
        saldo_fisico=physical,
        saldo_virtual_total=available,
        updated_at=STAMP,
    )
    return row


def capture():
    rows = [
        product("1", "real.phone"),
        product("2", "real.earphone"),
        product("3", "real.kit", "E"),
    ]
    components = [
        {
            "id": "c1",
            "kit_bling_product_id": "3",
            "component_bling_product_id": "1",
            "quantity": "2",
            "synced_at": STAMP,
        },
        {
            "id": "c2",
            "kit_bling_product_id": "3",
            "component_bling_product_id": "2",
            "quantity": "1",
            "synced_at": STAMP,
        },
    ]
    data = {
        "version": 1,
        "source": "davinci_mirror",
        "read_only": "on",
        "captured_at": STAMP,
        "products": rows,
        "components": components,
        "orders": [],
        "links": [],
        "integrations": [],
        "stores": [],
        "priorities": [],
        "coverage": {
            "products_total": 3,
            "orders_total": 0,
            "orders_eligible": 0,
            "orders_window_days": 30,
            "orders_limit": 10000,
            "links_total": 0,
            "links_limit": 100000,
        },
    }
    return data


@pytest.fixture
def real(tmp_path):
    erp = LocalERP(tmp_path / "real.sqlite3")
    reference = ReferenceStore(erp)
    reference.replace(capture())
    return erp, reference


def b(erp, sku="real.phone"):
    p = next(p for p in erp.snapshot()["products"] if p["sku"] == sku)
    return next(v for v in p["balances"] if v["depot"] == "geral")


def test_bootstrap_uses_real_identity_components_and_opening_reserves(real):
    erp, ref = real
    report = bootstrap_real_catalog(erp, ref)
    assert report["products"] == 3 and report["kits"] == 1
    assert Decimal(b(erp)["physical"]) == 8 and Decimal(b(erp)["reserved"]) == 2
    kit = next(p for p in erp.snapshot()["products"] if p["sku"] == "real.kit")
    assert kit["components"] == [
        {"sku": "real.earphone", "quantity": "1"},
        {"sku": "real.phone", "quantity": "2"},
    ]
    assert erp.snapshot()["orders"] == []


def test_reference_refresh_cannot_overwrite_working_erp_balances(real):
    erp, ref = real
    bootstrap_real_catalog(erp, ref)
    erp.command(
        "local",
        "stock.post",
        {
            "sku": "real.phone",
            "depot": "geral",
            "type": "E",
            "quantity": "4",
            "reason": "local purchase",
        },
    )
    newer = capture()
    newer["products"][0]["saldo_fisico"] = 100
    ref.replace(newer)
    assert Decimal(b(erp)["physical"]) == 12
    assert ref.rows("products", query="real.phone")["items"][0]["saldo_fisico"] == 100


def test_missing_stock_is_unknown_not_zero_and_blocks_orders(real):
    erp, ref = real
    data = capture()
    data["products"][0]["saldo_fisico"] = None
    data["products"][0]["saldo_virtual_total"] = None
    ref.replace(data)
    report = bootstrap_real_catalog(erp, ref)
    assert report["unknown_stock"] == 1
    assert b(erp)["physical"] is None and b(erp, "real.kit")["available"] is None
    with pytest.raises(ERPError) as exc:
        erp.command(
            "neworder",
            "order.import",
            {
                "channel": "local",
                "store": "test",
                "external_id": "one",
                "depot": "geral",
                "items": [{"sku": "real.phone", "quantity": "1"}],
            },
        )
    assert exc.value.code == "stock_review"


def test_shortage_is_preserved_and_can_be_improved_by_restocking(real):
    erp, ref = real
    data = capture()
    data["products"][0].update(saldo_fisico=1, saldo_virtual_total=-1)
    ref.replace(data)
    report = bootstrap_real_catalog(erp, ref)
    assert report["shortages"] == 1 and Decimal(b(erp)["available"]) == -1
    erp.command(
        "restock",
        "stock.post",
        {"sku": "real.phone", "depot": "geral", "type": "E", "quantity": "1", "reason": "restock"},
    )
    assert Decimal(b(erp)["available"]) == 0


def test_ambiguous_active_skus_are_not_silently_selected(real):
    erp, ref = real
    data = capture()
    data["products"].append(product("4", "real.phone"))
    data["coverage"]["products_total"] = 4
    ref.replace(data)
    report = bootstrap_real_catalog(erp, ref)
    assert any(i["code"] == "ambiguous_sku" for i in report["issues"])
    assert not any(p["sku"] == "real.phone" for p in erp.snapshot()["products"])


def test_bom_cycles_are_reported_without_sku_parsing(real):
    erp, ref = real
    data = capture()
    data["components"] = [
        {
            "id": "cycle",
            "kit_bling_product_id": "3",
            "component_bling_product_id": "3",
            "quantity": "1",
            "synced_at": STAMP,
        }
    ]
    ref.replace(data)
    report = bootstrap_real_catalog(erp, ref)
    assert report["issues"] == [{"sku": "real.kit", "code": "kit_cycle"}]
    assert b(erp, "real.kit")["available"] is None


def test_bootstrap_cannot_reset_an_existing_erp(real):
    erp, ref = real
    bootstrap_real_catalog(erp, ref)
    before = erp.snapshot()
    with pytest.raises(ERPError):
        bootstrap_real_catalog(erp, ref)
    assert erp.snapshot() == before


def test_catalog_review_also_blocks_direct_stock_movements(real):
    erp, ref = real
    data = capture()
    data["products"].append(product("4", "real.phone", status="E"))
    data["coverage"]["products_total"] = 4
    ref.replace(data)
    bootstrap_real_catalog(erp, ref)
    with pytest.raises(ERPError) as exc:
        erp.command(
            "bad-stock",
            "stock.post",
            {"sku": "real.phone", "depot": "geral", "type": "E", "quantity": "1", "reason": "test"},
        )
    assert exc.value.code == "catalog_review"


def test_credentials_or_extra_customer_fields_are_rejected(real):
    erp, ref = real
    before = ref.info()["snapshot"]
    for kind, key in [("products", "access_token"), ("integrations", "credentials")]:
        data = capture()
        if kind == "integrations":
            row = dict.fromkeys(FIELDS[kind])
            row["id"] = "integration"
            data[kind] = [row]
        data[kind][0][key] = "must never be stored"
        with pytest.raises(ERPError):
            ref.replace(data)
    assert ref.info()["snapshot"] == before


def test_failed_read_preserves_previous_reference_and_working_data(real):
    erp, ref = real
    bootstrap_real_catalog(erp, ref)
    before = erp.snapshot()

    def fail():
        raise ERPError("source_unavailable", "offline", 502)

    ref.source = SimpleNamespace(read=fail)
    with pytest.raises(ERPError):
        ref.refresh()
    assert erp.snapshot() == before
    assert ref.info()["last_attempt"]["ok"] == 0
    assert not ref.lock.locked()


def test_complete_catalog_count_is_verified_before_replacing(real):
    _erp, ref = real
    data = capture()
    data["coverage"]["products_total"] = 4
    with pytest.raises(ERPError):
        ref.replace(data)
    assert ref.info()["snapshot"]["counts"]["products"] == 3


def test_source_search_escapes_wildcards_and_handles_pagination(real):
    _erp, ref = real
    assert ref.rows("products", query="real.", limit=1, offset=1)["total"] == 3
    assert len(ref.rows("products", limit=1)["items"]) == 1
    assert ref.rows("products", query="%'")["total"] == 0


def test_ssh_source_uses_only_fixed_read_command_and_does_not_leak_stderr(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=255, stdout="", stderr="sensitive detail")

    monkeypatch.setattr("app.erp_local.reference.subprocess.run", run)
    with pytest.raises(ERPError) as exc:
        SSHReference("davinci-prod").read()
    assert "sensitive" not in str(exc.value)
    assert "default_transaction_read_only=on" in calls[0][0][-1]
    assert "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY" in calls[0][1]["input"]
    assert calls[0][1].get("shell") is None
    with pytest.raises(ERPError):
        SSHReference("-oProxyCommand=anything")


def live_capture():
    return {
        "source": "bling_api_read",
        "started_at": STAMP,
        "captured_at": STAMP,
        "db_read_only": "on",
        "no_token_refresh": True,
        "balances": [{"id": "1", "physical": "20", "available": "18", "deposits": []}],
        "deposits": [],
        "structures": [],
        "calls": [{"method": "GET", "path": "/estoques/saldos", "status": 200}],
        "failures": [],
    }


def test_initial_direct_read_enriches_only_the_initial_opening_stock(real):
    erp, ref = real
    report = bootstrap_real_catalog(erp, ref, live_capture())
    assert Decimal(b(erp)["physical"]) == 20
    assert report["direct_stock"] == 1 and report["mirror_stock"] == 1
    assert (
        next(p for p in erp.snapshot()["products"] if p["sku"] == "real.phone")["origin"][
            "stock_basis"
        ]
        == "bling_api_initial"
    )


def test_initial_read_rejects_writes_and_token_material():
    from app.erp_local.live_validation import validate_live_read

    data = live_capture()
    data["calls"][0]["method"] = "POST"
    with pytest.raises(ERPError):
        validate_live_read(data)
    data = live_capture()
    data["access_token"] = "synthetic"  # noqa: S105 - deliberately rejected test material.
    with pytest.raises(ERPError):
        validate_live_read(data)


def test_own_opening_prices_are_preserved_after_reference_changes(real):
    from app.erp_local.initial_load import bootstrap_missing_prices

    erp, ref = real
    data = capture()
    data["products"][0].update(price="12.3456", bling_cost_price="7.1234")
    ref.replace(data)
    bootstrap_real_catalog(erp, ref)
    data["products"][0]["price"] = "99.0000"
    ref.replace(data)
    bootstrap_missing_prices(erp, ref)
    own = next(p for p in erp.snapshot()["products"] if p["sku"] == "real.phone")
    assert own["price"] == "12.3456" and own["cost_price"] == "7.1234"
