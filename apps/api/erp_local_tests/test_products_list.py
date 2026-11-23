"""Lists read from outside the ERP screen (DaVinci's "Produtos 2" and "Pedidos 2")."""

from fastapi.testclient import TestClient

from app.erp_local.api import create_app


def test_a_page_of_products_with_stock_totals_search_and_kits(tmp_path):
    app = create_app(tmp_path / "erp.sqlite3")
    app.state.erp.command("seed", "demo.seed", {})
    client = TestClient(app, base_url="http://localhost")
    page = client.get("/api/erp-local/products/list?limit=2").json()
    assert page["total"] >= 3 and len(page["items"]) == 2 and page["offset"] == 0
    kit = next(
        i
        for i in client.get("/api/erp-local/products/list?q=demo.kit").json()["items"]
        if i["sku"] == "demo.kit"
    )
    assert kit["kind"] == "kit" and kit["reserved"] is None and kit["components"]
    simple = client.get("/api/erp-local/products/list?q=demo.aparelho").json()["items"][0]
    assert simple["sku"] == "demo.aparelho" and simple["physical"] is not None
    assert simple["available"] is not None and simple["status"] == "active"
    assert client.get("/api/erp-local/products/list?q=nada-assim").json() == {
        "items": [],
        "total": 0,
        "offset": 0,
    }


def test_store_sales_carry_blings_situation_names(tmp_path):
    client = TestClient(create_app(tmp_path / "erp.sqlite3"), base_url="http://localhost")
    reply = client.get("/api/erp-local/store-sales").json()
    assert reply["situations"]["6"] == "Em aberto" and reply["situations"]["12"] == "Cancelado"
