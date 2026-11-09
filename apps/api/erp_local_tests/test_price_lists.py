import pytest
from test_core import run
from test_sales import body, doc

from app.erp_local import price_lists
from app.erp_local.core import ERPError


def create(erp, **changes):
    return run(
        erp,
        "price_list.save",
        {
            "data": {
                "name": "Lista local",
                "mode": "percent",
                "action": "discount",
                "value": "10",
                **changes,
            }
        },
    )["id"]


def context(**changes):
    return {
        "date": "2026-11-05",
        "contact_id": "",
        "payment_method_id": "",
        "state": "",
        "channel": "local",
        "store": "manual",
        **changes,
    }


def quote(erp, identifier, skus=None, **changes):
    with erp.connection() as db:
        return price_lists.quote(erp, db, identifier, skus or ["demo.aparelho"], context(**changes))


def test_prices_are_explicit_and_frozen_when_catalog_or_list_changes(erp):
    run(
        erp, "product.update", {"sku": "demo.aparelho", "version": 0, "data": {"price": "100.0050"}}
    )
    identifier = create(erp)
    frozen = quote(erp, identifier)
    assert frozen["prices"] == {"demo.aparelho": "90.0045"}
    saved = body(
        price_list_id=identifier,
        price_quote_token=frozen["token"],
        items=[{"sku": "demo.aparelho", "quantity": "2", "unit_price": "90.0045"}],
    )
    order = run(erp, "order.save", saved)["id"]
    assert doc(erp, order)["data"]["total"] == "182.01"
    run(erp, "product.update", {"sku": "demo.aparelho", "version": 1, "data": {"price": "200"}})
    run(
        erp,
        "price_list.save",
        {"id": identifier, "version": 1, "data": {"name": "Alterada", "status": "inactive"}},
    )
    run(erp, "order.save", {**saved, "id": order, "version": 1})
    assert doc(erp, order)["data"]["price_quote"] == frozen
    with pytest.raises(ERPError, match="inativa"):
        run(erp, "order.save", {**saved, "external_id": "NEW"})


def test_quote_change_or_manual_price_override_blocks_save_without_reserving_stock(erp):
    run(erp, "product.update", {"sku": "demo.aparelho", "version": 0, "data": {"price": "100"}})
    identifier = create(erp)
    frozen = quote(erp, identifier)
    saved = body(
        price_list_id=identifier,
        price_quote_token=frozen["token"],
        status="reserved",
        items=[{"sku": "demo.aparelho", "quantity": "1", "unit_price": "1"}],
    )
    before = erp.snapshot()
    with pytest.raises(ERPError, match="diferem"):
        run(erp, "order.save", saved)
    assert erp.snapshot() == before
    run(erp, "product.update", {"sku": "demo.aparelho", "version": 1, "data": {"price": "200"}})
    with pytest.raises(ERPError, match="novamente"):
        run(
            erp,
            "order.save",
            {**saved, "items": [{"sku": "demo.aparelho", "quantity": "1", "unit_price": "90"}]},
        )


def test_custom_and_value_lists_require_complete_nonnegative_prices(erp):
    custom = create(erp, mode="custom", prices={"demo.aparelho": "0.3333"})
    assert quote(erp, custom)["prices"]["demo.aparelho"] == "0.3333"
    with pytest.raises(ERPError, match="sem preço"):
        quote(erp, custom, skus=["demo.fone"])
    value = create(erp, name="Desconto em reais", mode="value", value="20")
    with pytest.raises(ERPError, match="cadastro pendente"):
        quote(erp, value)
    run(erp, "product.update", {"sku": "demo.aparelho", "version": 0, "data": {"price": "10"}})
    with pytest.raises(ERPError, match="negativo"):
        quote(erp, value)


def test_scope_date_and_version_are_checked(erp):
    identifier = create(
        erp,
        mode="custom",
        prices={"demo.aparelho": "10"},
        scope="state",
        state="SP",
        valid_from="2026-11-01",
        valid_to="2026-11-30",
    )
    with pytest.raises(ERPError, match="não se aplica"):
        quote(erp, identifier, state="RJ")
    with pytest.raises(ERPError, match="vigência"):
        quote(erp, identifier, state="SP", date="2026-12-01")
    assert quote(erp, identifier, state="SP")["prices"]["demo.aparelho"] == "10.0000"
    with pytest.raises(ERPError, match="mudou"):
        run(erp, "price_list.save", {"id": identifier, "version": 0, "data": {"name": "X"}})


@pytest.mark.parametrize(
    "changes",
    [
        {"value": "101"},
        {"value": "-1"},
        {"mode": "custom", "prices": {}},
        {"scope": "store", "channel": "ml", "store": "nome-parecido"},
        {"scope": "contact", "contact_id": "unknown"},
        {"valid_from": "2026-12-01", "valid_to": "2026-11-01"},
    ],
)
def test_invalid_price_list_does_not_change_the_book(erp, changes):
    before = erp.snapshot()
    with pytest.raises(ERPError):
        create(erp, **changes)
    assert erp.snapshot() == before
