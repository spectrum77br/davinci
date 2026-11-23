"""Novos procedimentos 1/6, 2/6, 8 e 13 no ERP: IMEI, devolução avulsa, anúncios e lotes."""

import json

import pytest
from test_core import order, run
from test_returns import receive, setup

from app.erp_local import finance, listings, order_imeis
from app.erp_local.core import ERPError

PHONE = "490154203237518"  # check digit valid
PHONE2 = "356938035643809"


def test_imei_check_digit_dual_sim_and_one_order_per_imei(erp):
    assert order_imeis.valid(PHONE) and order_imeis.valid(PHONE2)
    assert not order_imeis.valid("490154203237519") and not order_imeis.valid("12345")
    first = order(erp, sku="demo.aparelho")["id"]
    run(erp, "order.imei.save", {"id": first, "sku": "demo.aparelho", "units": [[PHONE, PHONE2]]})
    with erp.connection() as db:
        assert order_imeis.snapshot(db)[first] == {"demo.aparelho": [[PHONE, PHONE2]]}
    second = order(erp, external="2", sku="demo.aparelho")["id"]
    with pytest.raises(ERPError, match="já está no pedido"):
        run(erp, "order.imei.save", {"id": second, "sku": "demo.aparelho", "units": [[PHONE]]})
    with pytest.raises(ERPError, match="inválido"):
        run(
            erp,
            "order.imei.save",
            {"id": second, "sku": "demo.aparelho", "units": [["490154203237519"]]},
        )
    with pytest.raises(ERPError, match="unidade"):
        run(
            erp,
            "order.imei.save",
            {"id": second, "sku": "demo.aparelho", "units": [[PHONE2], [PHONE]]},
        )


def _depot(erp, name, purpose):
    return run(erp, "depot.save", {"name": name, "purpose": purpose})["id"]


def test_a_return_comes_back_as_avulso_only_into_an_avulso_depot(erp):
    _, case, _ = setup(erp)
    avulso = _depot(erp, "Avulsos", "avulso")
    line = {"sku": "demo.aparelho", "quantity": "1", "condition": "avulso", "destination": "geral"}
    with pytest.raises(ERPError, match="avulsos"):
        receive(erp, case, lines=[line])
    receive(erp, case, lines=[{**line, "destination": avulso}])
    with pytest.raises(ERPError, match="depósito de venda"):
        receive(
            erp,
            case,
            reference="FISICO-2",
            lines=[{**line, "condition": "new", "destination": avulso}],
        )


def test_a_lost_piece_closes_only_with_its_outcome(erp, monkeypatch):
    _, case, _ = setup(erp)
    with pytest.raises(ERPError, match="recebimento físico"):
        run(erp, "returns.close", {"id": case, "financial": "hold"})
    monkeypatch.setenv("ERP_TRUST_PROXY", "1")
    with pytest.raises(ERPError, match="administrador"):
        run(erp, "returns.close", {"id": case, "financial": "hold", "outcome": "extraviado"})
    monkeypatch.delenv("ERP_TRUST_PROXY")
    run(erp, "returns.close", {"id": case, "financial": "hold", "outcome": "extraviado"})
    with erp.connection() as db:
        data = json.loads(
            db.execute("SELECT data FROM return_cases WHERE id=?", (case,)).fetchone()[0]
        )
    assert data["outcome"] == "extraviado"


def _reference(erp, **kinds):
    with erp.connection() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS reference_rows (kind TEXT, id TEXT, data TEXT NOT NULL,"
            " search TEXT NOT NULL, PRIMARY KEY(kind,id))"
        )
        for kind, rows in kinds.items():
            for row in rows:
                db.execute(
                    "INSERT OR REPLACE INTO reference_rows VALUES (?,?,?,'')",
                    (kind, row["id"], json.dumps(row)),
                )


def _link(identifier, product, store="i1", sku=""):
    return {
        "id": identifier,
        "product_id": product,
        "integration_id": store,
        "platform": "shopee",
        "external_id": "L" + identifier,
        "variation_id": "",
        "external_sku": sku,
        "listing_title": "Anúncio",
        "stock": 1,
        "price": "10",
    }


def test_listings_show_their_link_and_organize_hides_only_invalid_ones(erp):
    _reference(
        erp,
        products=[
            {"id": "p1", "sku": "demo.aparelho", "situacao": "A"},
            {"id": "p2", "sku": "nao.existe", "situacao": "A"},
            {"id": "p3", "sku": "b056.18.20", "situacao": "E"},
        ],
        integrations=[
            {"id": "i1", "name": "Loja", "archived_at": None},
            {"id": "i2", "name": "Banida", "archived_at": "2026-11-09"},
        ],
        links=[
            _link("1", "p1", sku="demo.aparelho"),
            _link("2", "p1", sku="outro.sku"),
            _link("3", "p2"),
            _link("4", "p1", store="i2"),
            _link("5", "p3", sku="b056.18.20"),
            {**_link("6", "p1", sku="demo.aparelho"), "platform": "bling"},
        ],
    )
    with erp.connection() as db:
        states = {r["id"]: r["state"] for r in listings.classify(db)}
    assert states == {
        "1": "linked",
        "2": "sku_differs",
        "3": "not_in_erp",
        "4": "archived",
        "5": "excluded",
    }
    with pytest.raises(ERPError, match="inválidos"):
        run(erp, "product.listings.organize", {"states": ["linked"]})
    assert (
        run(erp, "product.listings.organize", {"states": ["not_in_erp", "archived"]})["hidden"] == 2
    )
    with erp.connection() as db:
        view = listings.overview(db)
        assert view["counts"]["hidden"] == 2 and {r["id"] for r in view["rows"]} == {"1", "2", "5"}
        assert {r["id"] for r in listings.overview(db, hidden=True)["rows"]} == {"3", "4"}
    run(erp, "product.listings.show", {"ids": ["3"]})
    with erp.connection() as db:
        assert listings.overview(db)["counts"]["hidden"] == 1


def _payable(erp, key, **extra):
    return run(
        erp,
        "finance.entry.create",
        {
            "kind": "payable",
            "reference": key,
            "party": "Fornecedor",
            "amount": "1000",
            "issued_on": "2026-11-17",
            "competence_on": "2026-11-17",
            "due_date": "2026-11-30",
            "category": "Estoque",
            **extra,
        },
    )


def test_payable_split_by_cost_center_and_lot_difference(erp):
    with pytest.raises(ERPError, match="100%"):
        _payable(erp, "A1", cost_centers={"celular": "50", "mala": "30"})
    with pytest.raises(ERPError, match="Celular, Eletro e Mala"):  # M-23: ordem alfabética
        _payable(erp, "A2", cost_centers={"geral": "100"})
    _payable(
        erp,
        "A3",
        cost_centers={"celular": "50", "mala": "30", "eletro": "20"},
        lot_number="LOTE-7",
        lot_value="900",
        exchange_rate="5.4321",
    )
    with erp.connection() as db:
        installment = next(
            i for i in finance.snapshot(db)["installments"] if as_decimal(i["amount"]) == 1000
        )
    run(
        erp,
        "finance.pay",
        {"installment_id": installment["id"], "amount": "1000", "external_id": "PAGO-LOTE-7"},
    )
    with erp.connection() as db:
        lot = finance.lots(db)[0]
    assert lot["lot"] == "LOTE-7" and as_decimal(lot["paid"]) == 1000
    assert float(lot["difference"]) == -100 and lot["exchange_rate"] == "5.4321"


def as_decimal(value):
    from decimal import Decimal

    return Decimal(str(value))


def test_stock_payable_needs_its_lot(erp):
    with pytest.raises(ERPError, match="número do lote"):
        _payable(erp, "L1", cost_centers={"mala": "100"})
    with pytest.raises(ERPError, match="número do lote"):
        _payable(erp, "L2", category=" estoque ")


def test_management_balance_counts_a_bill_on_the_day_it_was_paid(erp):
    _payable(
        erp,
        "F1",
        category="Frete",
        due_date="2026-11-30",
        cost_centers={"celular": "50", "mala": "50"},
    )
    _payable(erp, "S1", lot_number="LOTE-9")  # stock: not an expense
    with erp.connection() as db:
        installment = next(
            i
            for i in finance.snapshot(db)["installments"]
            if as_decimal(i["amount"]) == 1000 and i["due_date"] == "2026-11-30"
        )
    run(
        erp,
        "finance.pay",
        {
            "installment_id": installment["id"],
            "amount": "400",
            "external_id": "PG-F1",
            "date": "2026-12-05",
        },
    )
    with erp.connection() as db:
        rows = finance.management(db)
    assert {
        (r["month"], r["category"], r["cost_center"], r["paid"], r["forecast"]) for r in rows
    } == {
        ("2026-12", "Frete", "Celular", "200.00", "0.00"),
        ("2026-12", "Frete", "Mala", "200.00", "0.00"),
        ("2026-11", "Frete", "Celular", "0.00", "300.00"),
        ("2026-11", "Frete", "Mala", "0.00", "300.00"),
    }
