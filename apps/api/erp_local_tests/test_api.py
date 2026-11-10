from uuid import uuid4

from fastapi.testclient import TestClient

from app.erp_local.api import create_app


def client(tmp_path):
    return TestClient(create_app(tmp_path / "api.sqlite3"), base_url="http://127.0.0.1:8765")


def test_serves_ui_and_local_health(tmp_path):
    with client(tmp_path) as c:
        assert "ERP local" in c.get("/").text
        assert c.get("/api/health").json()["external_connections"] is False
        assert c.get("/assets/app.js").status_code == 200
        assert c.get("/assets/style.css").status_code == 200


def test_stock_allocation_read_does_not_mutate_and_cross_origin_is_refused(tmp_path):
    from test_core import run
    from test_stock_allocation import sale

    with client(tmp_path) as c:
        erp = c.app.state.erp
        run(erp, "demo.seed", {})
        identifier = sale(erp)
        before = erp.snapshot()
        url = f"/api/erp-local/orders/{identifier}/stock-allocation"
        response = c.get(url)
        assert response.status_code == 200 and response.json()["version"] == 1
        assert response.json()["items"][0]["commercial_sku"] == "a.ci+f.ci"
        assert c.get(url, headers={"Origin": "https://evil.invalid"}).status_code == 403
        assert erp.snapshot() == before


def test_local_command_and_snapshot(tmp_path):
    with client(tmp_path) as c:
        r = c.post(
            "/api/erp-local/commands",
            json={"operation_key": str(uuid4()), "kind": "demo.seed", "payload": {}},
            headers={"X-ERP-Local": "1"},
        )
        assert r.status_code == 200
        assert len(c.get("/api/erp-local/state").json()["products"]) == 3


def test_cross_origin_commands_are_rejected(tmp_path):
    with client(tmp_path) as c:
        r = c.post(
            "/api/erp-local/commands",
            json={"operation_key": "x", "kind": "demo.seed"},
            headers={"X-ERP-Local": "1", "Origin": "https://example.com"},
        )
        assert r.status_code == 403 and r.json()["code"] == "foreign_origin"
        assert c.get("/api/erp-local/state").json()["products"] == []


def test_browser_form_posts_without_local_header_are_rejected(tmp_path):
    with client(tmp_path) as c:
        assert (
            c.post(
                "/api/erp-local/commands", json={"operation_key": "x", "kind": "demo.seed"}
            ).status_code
            == 403
        )


def test_external_actions_and_tokens_are_not_accepted(tmp_path):
    with client(tmp_path) as c:
        headers = {"X-ERP-Local": "1"}
        r = c.post(
            "/api/erp-local/commands",
            json={"operation_key": "x", "kind": "bling.publish", "payload": {}},
            headers=headers,
        )
        assert r.status_code == 422
        assert r.json()["code"] == "unsupported_command"
        assert (
            c.post(
                "/api/erp-local/commands",
                json={"operation_key": "y", "kind": "demo.seed", "access_token": "synthetic"},
                headers=headers,
            ).status_code
            == 422
        )


def test_oversized_payload_is_rejected_before_execution(tmp_path):
    with client(tmp_path) as c:
        r = c.post(
            "/api/erp-local/commands",
            json={"operation_key": "x", "kind": "demo.seed", "payload": {"x": "a" * 256001}},
            headers={"X-ERP-Local": "1"},
        )
        assert r.status_code == 413


def test_logistics_upload_download_and_origin_protection(tmp_path):
    from test_logistics import packed, prepared

    with client(tmp_path) as c:
        erp = c.app.state.erp
        erp.command("seed", "demo.seed", {})
        identifier = packed(erp, prepared(erp))
        raw = b"^XA^FDTESTE SEM POSTAGEM^FS^XZ"
        url = f"/api/erp-local/logistics/{identifier}/labels?format=zpl"
        assert c.post(url, content=raw).status_code == 403
        assert (
            c.post(
                url, content=raw, headers={"X-ERP-Local": "1", "Origin": "https://example.com"}
            ).status_code
            == 403
        )
        response = c.post(url, content=raw, headers={"X-ERP-Local": "1"})
        assert response.status_code == 200 and response.json()["verified"] is False
        result = c.get("/api/erp-local/logistics/labels/" + response.json()["id"])
        assert result.content == raw and "attachment" in result.headers["content-disposition"]
        state = c.get("/api/erp-local/state").json()
        assert state["logistics"]["external_writes"] is False
        assert "content" not in state["logistics"]["labels"][0]
        assert (
            c.post(
                url, content=b"x" * (2 * 1024 * 1024 + 1), headers={"X-ERP-Local": "1"}
            ).status_code
            == 413
        )


def test_release_check_and_csv_do_not_execute_commands(tmp_path):
    from test_core import order

    with client(tmp_path) as c:
        erp = c.app.state.erp
        erp.command("seed", "demo.seed", {})
        order(erp, external="=SUM(1+1)", store="@formula")
        before = erp.snapshot()
        report = c.get("/api/erp-local/release-check").json()
        assert report["ledger_ok"] and report["production_ready"] is False
        csv = c.get("/api/erp-local/orders.csv")
        assert "'=SUM(1+1)" in csv.text and "'@formula" in csv.text
        assert erp.snapshot() == before


def test_selected_order_print_escapes_customer_and_excludes_other_orders(tmp_path):
    from test_core import order, run

    with client(tmp_path) as c:
        erp = c.app.state.erp
        erp.command("seed", "demo.seed", {})
        selected = run(
            erp,
            "order.import",
            {
                "channel": "local",
                "store": "demo",
                "external_id": "P1",
                "depot": "geral",
                "items": [{"sku": "demo.kit", "quantity": "1", "unit_price": "10"}],
                "customer_name": "<script>never()</script>",
                "date": "2026-11-05",
                "expected_date": "2026-11-07",
            },
        )
        order(erp, external="UNSELECTED")
        before = erp.snapshot()
        response = c.get("/api/erp-local/orders/print", params={"ids": selected["id"]})
        assert response.status_code == 200
        assert "&lt;script&gt;never()&lt;/script&gt;" in response.text
        assert "<script>never()" not in response.text and "UNSELECTED" not in response.text
        assert erp.snapshot() == before
        assert c.get("/api/erp-local/orders/print", params={"ids": "absent"}).status_code == 404
        one = f"/api/erp-local/orders/{selected['id']}/print"
        detail = c.get(one, params={"mode": "detail"})
        receipt = c.get(one, params={"mode": "receipt"})
        assert detail.status_code == 200 and "Pedido de venda" in detail.text
        assert "demo.kit" in detail.text and "&lt;script&gt;" in detail.text
        assert receipt.status_code == 200 and "Comprovante do pedido" in receipt.text
        assert c.get(one, params={"mode": "carne"}).status_code == 404


def test_purchase_exports_preserve_selection_escape_content_and_do_not_post_effects(tmp_path):
    from test_purchase_documents import purchase

    with client(tmp_path) as c:
        erp = c.app.state.erp
        erp.command("seed", "demo.seed", {})
        selected, _ = purchase(
            erp,
            supplier="=FORMULA",
            notes="<script>never()</script>",
            internal_notes="PRIVATE-INTERNAL-NOTE",
            purchase_order="@formula",
        )
        purchase(erp, "OTHER-UNSELECTED", supplier="Outro fornecedor")
        before = erp.snapshot()
        output = c.get("/api/erp-local/purchases/print", params={"ids": selected["id"]})
        assert output.status_code == 200
        assert "&lt;script&gt;never()&lt;/script&gt;" in output.text
        assert "<script>never()" not in output.text
        assert "PRIVATE-INTERNAL-NOTE" not in output.text and "OTHER-UNSELECTED" not in output.text
        assert "05/11/2026" in output.text and "R$ 20,00" in output.text
        assert "Situação: Em aberto" in output.text
        csv = c.get("/api/erp-local/purchases.csv")
        assert "'=FORMULA" in csv.text and "'@formula" in csv.text
        assert c.get("/api/erp-local/purchases/print", params={"ids": "absent"}).status_code == 404
        assert erp.snapshot() == before


def test_stock_history_reconstructs_balance_per_depot_without_business_writes(tmp_path):
    from test_core import run

    with client(tmp_path) as c:
        erp = c.app.state.erp
        erp.command("seed", "demo.seed", {})
        run(
            erp,
            "stock.transfer",
            {
                "sku": "demo.aparelho",
                "depot": "geral",
                "destination": "retornos",
                "quantity": "2",
                "reason": "Transferência local",
            },
        )
        before = erp.snapshot()
        geral = c.get(
            "/api/erp-local/stock-movements", params={"sku": "demo.aparelho", "depot": "geral"}
        ).json()
        retornos = c.get(
            "/api/erp-local/stock-movements", params={"sku": "demo.aparelho", "depot": "retornos"}
        ).json()
        all_depots = c.get("/api/erp-local/stock-movements", params={"sku": "demo.aparelho"}).json()
        assert float(geral["items"][0]["balance"]) == 6
        assert float(retornos["items"][0]["balance"]) == 2
        assert float(all_depots["items"][0]["balance"]) == 8
        assert erp.snapshot() == before
        assert c.get("/api/erp-local/stock-movements", params={"sku": "unknown"}).status_code == 404


def test_channel_intake_is_local_simulation_and_rejects_foreign_origin(tmp_path):
    from test_channels import event

    with client(tmp_path) as c:
        headers = {"X-ERP-Local": "1"}
        c.post(
            "/api/erp-local/commands",
            json={"operation_key": "seed", "kind": "demo.seed", "payload": {}},
            headers=headers,
        )
        response = c.post("/api/erp-local/channels/events", json=event(), headers=headers)
        assert response.status_code == 200 and response.json()["source"] == "simulation"
        assert c.get("/api/erp-local/state").json()["channels"]["external_writes"] is False
        assert (
            c.post(
                "/api/erp-local/channels/events",
                json=event(),
                headers={**headers, "Origin": "https://example.com"},
            ).status_code
            == 403
        )
        assert (
            c.post("/api/erp-local/channels/publish", json={}, headers=headers).status_code == 404
        )


def test_app_start_recovers_durable_unprocessed_receipts(tmp_path, monkeypatch):
    from test_channels import event

    database = tmp_path / "restart.sqlite3"
    application = create_app(database)
    application.state.erp.command("seed", "demo.seed", {})
    flow = application.state.channels

    def interrupted(_identifier):
        raise RuntimeError("interrupted")

    monkeypatch.setattr(flow, "process", interrupted)
    import pytest

    with pytest.raises(RuntimeError):
        flow.receive(event())
    with TestClient(create_app(database), base_url="http://127.0.0.1:8765") as c:
        state = c.get("/api/erp-local/state").json()
        assert state["channels"]["inbox_counts"] == {"processed": 1}
        assert len(state["orders"]) == 1


def test_stock_entry_keeps_the_purchase_price_in_the_history(tmp_path):
    from app.erp_local.core import LocalERP

    database = tmp_path / "price.sqlite3"
    erp = LocalERP(database)
    erp.command("seed", "demo.seed", {})
    erp.command(
        "e1",
        "stock.post",
        {
            "sku": "demo.aparelho",
            "depot": "geral",
            "type": "E",
            "quantity": "2",
            "reason": "compra",
            "unit_cost": "40",
            "purchase_price": "38.5",
        },
    )
    with TestClient(create_app(database), base_url="http://127.0.0.1:8765") as c:
        items = c.get("/api/erp-local/stock-movements", params={"sku": "demo.aparelho"}).json()[
            "items"
        ]
    entry = next(i for i in items if i["reason"] == "compra")
    assert entry["purchase_price"] == "38.5" and float(entry["unit_cost"]) == 40
