"""Several issuers: read from notes, adopted on purpose, chosen per store and kind of product."""

import pytest

from app.erp_local import multi_company as mc
from app.erp_local.core import ERPError

CNPJ = "11222333000181"
TECHNICAL = {"cnpj": "11444777000161", "contact": "Dev", "email": "d@x.com", "phone": "1199999"}
EMIT = (
    f"<emit><CNPJ>{CNPJ}</CNPJ><xNome>Loja LTDA</xNome><xFant>Loja</xFant><enderEmit>"
    "<xLgr>Rua A</xLgr><nro>1</nro><xBairro>Centro</xBairro><cMun>3550308</cMun>"
    "<xMun>São Paulo</xMun><UF>SP</UF><CEP>01001000</CEP></enderEmit><IE>123456</IE>"
    "<CRT>1</CRT></emit>"
)
READ = {
    "emit": [{"cnpj": CNPJ, "emit": EMIT}],
    "stats": [
        {"cnpj": CNPJ, "serie": "1", "ultimo": 10, "ult30": 0, "ultima": "2026-01-01"},
        {"cnpj": CNPJ, "serie": "4", "ultimo": 500, "ult30": 7, "ultima": "2026-11-05"},
    ],
}


def load(erp):
    erp.command("c1", "fiscal.candidates.load", {"rows": mc.candidates_from_read(READ)})


def adopt(erp, op="a1"):
    return erp.command(op, "fiscal.company.adopt", {"cnpj": CNPJ})


def stored(erp):
    with erp.connection() as db:
        return mc.snapshot(db)


def test_read_keeps_issuer_data_and_numbering_of_the_busiest_series():
    row = mc.candidates_from_read(READ)[0]
    assert row["name"] == "Loja LTDA" and row["crt"] == "1" and row["address"]["uf"] == "SP"
    assert row["series"] == 4 and row["last_number"] == 500 and row["notes_30_days"] == 7


def test_loading_issues_nothing_and_adopting_needs_a_technical_contact(erp):
    load(erp)
    assert stored(erp)["candidates"][0]["company_id"] is None
    with pytest.raises(ERPError, match="responsável técnico"):
        adopt(erp)


def test_adopted_company_continues_the_real_numbering(erp):
    import json

    load(erp)
    erp.command("t1", "fiscal.technical.save", TECHNICAL)
    result = adopt(erp)
    with erp.connection() as db:
        data = json.loads(
            db.execute("SELECT data FROM fiscal_companies WHERE id=?", (result["id"],)).fetchone()[
                0
            ]
        )
    assert (
        data["next_number"] == 501 and data["series"] == 4 and data["technical"]["contact"] == "Dev"
    )
    assert stored(erp)["candidates"][0]["company_id"] == result["id"]


def test_adopting_again_never_lowers_the_number(erp):
    import json

    load(erp)
    erp.command("t1", "fiscal.technical.save", TECHNICAL)
    first = adopt(erp)
    with erp.connection() as db:
        db.execute(
            "UPDATE fiscal_companies SET data=json_set(data,'$.next_number',900) WHERE id=?",
            (first["id"],),
        )
    second = adopt(erp, "a2")
    assert second["id"] == first["id"]
    with erp.connection() as db:
        data = json.loads(db.execute("SELECT data FROM fiscal_companies").fetchone()[0])
    assert data["next_number"] == 900


def order(*skus, channel="shopee", store="mini"):
    return {"channel": channel, "store": store, "items": [{"sku": s} for s in skus]}


@pytest.fixture
def routed(erp, monkeypatch):
    load(erp)
    erp.command("t1", "fiscal.technical.save", TECHNICAL)
    company = adopt(erp)["id"]
    kinds = {"mala.1": "mala", "mala.2": "mala", "cel.1": "celular", "x": ""}
    monkeypatch.setattr(mc, "_item_type", lambda _db, sku: kinds[sku])
    return erp, company


def route(erp, op, product_type, company):
    erp.command(
        op,
        "fiscal.route.save",
        {"channel": "shopee", "store": "mini", "product_type": product_type, "company_id": company},
    )


def test_store_company_applies_without_a_kind_rule(routed):
    erp, company = routed
    route(erp, "r1", "", company)
    with erp.connection() as db:
        assert mc.route_for_order(db, order("x")) == company
        assert mc.route_for_order(db, order("x", store="outra")) is None
        assert mc.route_for_order(db, {"items": []}) is None


def test_kind_rule_only_when_the_whole_order_is_that_kind(routed):
    erp, company = routed
    with erp.connection() as db:
        db.execute(
            "INSERT INTO fiscal_companies SELECT 'outra',version,"
            "json_set(data,'$.cnpj','11444777000161'),updated_at FROM fiscal_companies"
        )
    route(erp, "r1", "", company)
    route(erp, "r2", "mala", "outra")
    with erp.connection() as db:
        assert mc.route_for_order(db, order("mala.1", "mala.2")) == "outra"
        assert mc.route_for_order(db, order("mala.1", "cel.1")) == company


def test_route_refuses_unknown_kind_and_company_and_can_be_removed(routed):
    erp, company = routed
    with pytest.raises(ERPError, match="Tipo"):
        route(erp, "r1", "gato", company)
    with pytest.raises(ERPError, match="não encontrada"):
        route(erp, "r2", "", "nada")
    route(erp, "r3", "", company)
    route(erp, "r4", "", "")
    assert stored(erp)["routes"] == []


def test_invoice_without_company_and_without_route_is_refused(erp):
    created = erp.command(
        "o1",
        "order.import",
        {
            "channel": "shopee",
            "store": "mini",
            "external_id": "X1",
            "depot": "geral",
            "items": [{"sku": "demo.aparelho", "quantity": "1", "unit_price": "10"}],
            "customer_name": "Ana",
        },
    )
    with pytest.raises(ERPError, match="empresa emissora"):
        erp.command("p1", "fiscal.invoice.prepare", {"order_id": created["id"]})


def test_kind_comes_from_the_deepest_category(erp):
    saved = erp.command("k1", "category.save", {"data": {"name": "Mala Usada"}})
    erp.command(
        "k2",
        "category.assign",
        {"items": [{"sku": "demo.aparelho", "version": 0, "category_id": saved["id"]}]},
    )
    with erp.connection() as db:
        assert mc._item_type(db, "demo.aparelho") == "mala"
