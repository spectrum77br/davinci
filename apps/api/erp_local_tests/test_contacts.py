import pytest
from test_core import balance, run

from app.erp_local import contacts
from app.erp_local.core import ERPError


def save(erp, **fields):
    return run(erp, "contact.save", {"data": {"name": "Cliente demonstrativo", **fields}})


def test_contact_uses_existing_validator_including_alphanumeric_cnpj_and_code(erp):
    contact = save(
        erp, person_type="J", document="12.ABC.345/01DE-35", roles=["customer", "supplier"]
    )
    with erp.connection() as db:
        data = contacts.snapshot(db)[0]
    assert data["code"] == 1 and data["version"] == 1
    assert data["data"]["document"] == "12ABC34501DE35"
    assert data["data"]["roles"] == ["customer", "supplier"]
    with pytest.raises(ERPError, match="outro contato"):
        save(erp, person_type="J", document="12ABC34501DE35")
    assert contact["external_writes"] is False


def test_contact_updates_are_versioned_and_never_overwrite_order_address(erp):
    contact = save(erp, street="Rua de demonstração", number="10", state="SP", city="Cidade teste")
    before = balance(erp)
    payload = {
        "channel": "local",
        "store": "demo",
        "external_id": "CONTACT-ORDER-1",
        "depot": "geral",
        "contact_id": contact["id"],
        "items": [{"sku": "demo.aparelho", "quantity": "1", "unit_price": "20"}],
    }
    result = run(erp, "order.import", payload)
    changed = run(
        erp,
        "contact.save",
        {
            "id": contact["id"],
            "version": 1,
            "data": {"name": "Nome atualizado", "street": "Outro endereço", "number": "20"},
        },
    )
    assert changed["version"] == 2
    with pytest.raises(ERPError, match="mudou"):
        run(erp, "contact.save", {"id": contact["id"], "version": 1, "data": {"name": "Obsoleto"}})
    assert run(erp, "order.import", payload)["duplicate"]
    order = erp.snapshot()["orders"][0]
    assert order["id"] == result["id"] and order["data"]["customer_name"] == "Cliente demonstrativo"
    assert order["data"]["contact"]["street"] == "Rua de demonstração"
    run(erp, "order.cancel", {"id": result["id"]})
    assert balance(erp) == before


def test_contact_inactive_blocks_new_orders_but_keeps_existing_documents(erp):
    contact = save(erp, status="inactive")
    before = erp.snapshot()
    with pytest.raises(ERPError, match="Ative"):
        run(
            erp,
            "order.import",
            {
                "channel": "local",
                "store": "demo",
                "external_id": "C1",
                "depot": "geral",
                "contact_id": contact["id"],
                "items": [{"sku": "demo.aparelho", "quantity": "1"}],
            },
        )
    assert erp.snapshot() == before


@pytest.mark.parametrize(
    "fields",
    [
        {"document": "123"},
        {"person_type": "J", "document": "12ABC34501DE34"},
        {"person_type": "E"},
        {"postal_code": "123"},
        {"state": "ZZ"},
        {"email": "not-an-email"},
        {"roles": ["verified_supplier"]},
        {"customer_since": "2026-02-30"},
        {"ibge_code": "123456"},
    ],
)
def test_invalid_contact_is_atomic_and_does_not_acknowledge_supplier_notes(erp, fields):
    before = erp.snapshot()
    with pytest.raises(ERPError):
        save(erp, **fields)
    assert erp.snapshot() == before
    with erp.connection() as db:
        assert not contacts.snapshot(db)


def test_foreign_document_is_scoped_by_country(erp):
    save(erp, person_type="E", country="Portugal", document="LOCAL-123")
    save(erp, person_type="E", country="Espanha", document="LOCAL-123")
    with erp.connection() as db:
        assert len(contacts.snapshot(db)) == 2
