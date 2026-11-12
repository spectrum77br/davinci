"""Every Bling sale looked up in what the ERP read, store by store."""

from fastapi.testclient import TestClient

from app.erp_local import bling_coverage, homolog, native_ingest, parallel_check
from app.erp_local.api import create_app

STORE = "11111111-1111-1111-1111-111111111111"


def sale(number, store_number, *, bling_store="200", situation="15", total="100.00", **extra):
    return {
        "number": number,
        "store_number": store_number,
        "bling_store": bling_store,
        "store": STORE,
        "channel": "shopee",
        "name": "Loja A",
        "day": "2026-11-10",
        "total": total,
        "situation": situation,
        **extra,
    }


def unit(status="READY_TO_SHIP"):
    return {"o": {"status": status, "items": []}}


def test_each_bling_store_shows_found_missing_and_value(erp, monkeypatch):
    units = {"A1": unit(), "A2": unit(), "A5": unit("CANCELLED")}
    monkeypatch.setattr(native_ingest, "_units", lambda db, c, s: units)
    totals = {"A1": "100.00", "A2": "90.00"}
    monkeypatch.setattr(parallel_check, "sale_total", lambda db, c, s, k, live: totals[k])
    orders = [
        sale("1", "A1"),
        sale("2", "A2"),
        sale("3", "A3"),
        sale("4", "A4", situation="12"),
        sale("5", "A5", situation="83957"),
        sale("6", "PO-029-1", bling_store="300", store=None, channel=None, name=None),
        sale("7", "", bling_store="400", store=None, channel=None, name=None),
    ]
    with erp.connection() as db:
        report = bling_coverage.compare(db, orders, {("shopee", STORE): "complete"})
    by_store = {s["bling_store"]: s for s in report["stores"]}
    shop = by_store["200"]
    assert (shop["bling"], shop["found"], shop["missing"], shop["cancelled"]) == (5, 3, 1, 1)
    assert (shop["equal"], shop["different"]) == (1, 1)
    assert shop["missing_list"][0]["store_number"] == "A3"
    assert shop["different_list"][0] == {
        "number": "2",
        "store_number": "A2",
        "day": "2026-11-10",
        "total": "100.00",
        "situation": "Em andamento",
        "erp_total": "90.00",
    }
    assert shop["reading"] == "complete"
    assert by_store["300"]["name"] == "Temu" and by_store["300"]["reading"] is None
    assert by_store["300"]["missing"] == 1
    assert by_store["400"]["name"].startswith("pedidos digitados")
    assert report["totals"]["missing"] == 3


def test_the_report_is_kept_shown_and_followed_by_the_homologation(tmp_path, monkeypatch):
    monkeypatch.setattr(native_ingest, "_units", lambda db, c, s: {})
    app = create_app(tmp_path / "erp.sqlite3")
    erp = app.state.erp
    for _ in range(3):
        result = bling_coverage.run(erp, reader=lambda: [sale("1", "A1")])
    assert result["missing"] == 1
    client = TestClient(app, base_url="http://localhost")
    shown = client.get("/api/erp-local/state").json()["bling_coverage"]
    assert shown["stores"][0]["missing_list"][0]["number"] == "1"
    assert homolog.FOLLOW["bling_coverage"] == ("mirror", None)


def test_a_bling_store_davinci_never_linked_is_found_by_its_sale_numbers(erp, monkeypatch):
    with erp.connection() as db:
        db.execute("INSERT INTO native_order_reads VALUES ('d1','carrefour','2026-11-11','{}')")
        db.execute(
            "INSERT INTO native_order_observations VALUES ('o1','d1','carrefour',?,"
            "'5778955150001-A','2026-11-11T10:00:00+00:00','2026-11-11T10:01:00+00:00','{}')",
            (STORE,),
        )
    units = {"5778955150001-A": unit("SHIPPING")}
    monkeypatch.setattr(native_ingest, "_units", lambda db, c, s: units)
    monkeypatch.setattr(parallel_check, "sale_total", lambda *a: "363.45")
    orders = [
        sale(
            "1",
            "5778955150001-A",
            bling_store="206354372",
            store=None,
            channel=None,
            name=None,
            total="363.45",
        ),
        sale("2", "PO-029-9", bling_store="205925390", store=None, channel=None, name=None),
    ]
    with erp.connection() as db:
        report = bling_coverage.compare(db, orders, {("carrefour", STORE): "complete"})
    by_store = {s["bling_store"]: s for s in report["stores"]}
    carrefour = by_store["206354372"]
    assert (carrefour["channel"], carrefour["found"], carrefour["missing"]) == ("carrefour", 1, 0)
    assert carrefour["reading"] == "complete" and carrefour["equal"] == 1
    assert "sem vínculo" in carrefour["name"]
    assert by_store["205925390"]["name"] == "Temu" and by_store["205925390"]["missing"] == 1
