"""Consulta: the screen only reads while Bling owns the real base."""

from uuid import uuid4

from fastapi.testclient import TestClient

from app.erp_local import bling_mirror, consulta
from app.erp_local.api import create_app

LOCAL = {"X-ERP-Local": "1"}


def app_client(tmp_path):
    return TestClient(create_app(tmp_path / "api.sqlite3"), base_url="http://127.0.0.1:8765")


def mirrored(erp):
    """As on the server: the worker keeps Bling's stock beside the ERP's."""
    with erp.connection() as db:
        db.executescript(bling_mirror.SCHEMA)
        db.execute("INSERT INTO bling_mirror_stock VALUES ('demo.aparelho','5','1','2026-11-18')")


def switch_a_store(erp):
    with erp.connection() as db:
        columns = [r[1] for r in db.execute("PRAGMA table_info(ingest_accounts)")]
        values = {"channel": "ml", "store": "c", "enabled": 1, "authority": "own", "failures": 0}
        db.execute(
            f"INSERT INTO ingest_accounts({','.join(columns)}) "  # noqa: S608
            f"VALUES ({','.join('?' * len(columns))})",
            [values.get(c, "2026-11-18") for c in columns],
        )


def seed(c):
    return change(c, "demo.seed", {})


def change(c, kind="category.save", payload=None):
    payload = {"data": {"name": "Categoria " + uuid4().hex[:8]}} if payload is None else payload
    return c.post(
        "/api/erp-local/commands",
        json={"operation_key": str(uuid4()), "kind": kind, "payload": payload},
        headers=LOCAL,
    )


def test_while_bling_owns_every_change_is_refused_and_reads_still_answer(tmp_path, monkeypatch):
    monkeypatch.delenv(consulta.ENV, raising=False)
    with app_client(tmp_path) as c:
        erp = c.app.state.erp
        assert seed(c).status_code == 200
        mirrored(erp)
        before = erp.snapshot()
        state = c.get("/api/erp-local/state").json()
        assert state["consulta"]["active"] is True and state["consulta"]["reason"] == "automático"
        refused = change(c)
        assert refused.status_code == 409 and refused.json()["code"] == "consulta"
        assert refused.json()["message"].startswith("Modo consulta: o Bling manda")
        for path in ("/api/erp-local/store-sales/labels", "/api/erp-local/listings/refresh"):
            assert c.post(path, json={"items": []}, headers=LOCAL).json()["code"] == "consulta"
        compat = c.post("/api/erp-local/bling/v3/produtos", json={}, headers=LOCAL)
        assert compat.status_code == 409 and compat.json()["error"]["type"] == "CONSULTA"
        papers = c.post(
            "/api/erp-local/store-sales/papers/sheet", json={"items": []}, headers=LOCAL
        )
        assert papers.json().get("code") != "consulta"  # refused by its own check, not here
        preview = c.post("/api/erp-local/price-preview", json={}, headers=LOCAL)
        assert preview.status_code != 409 or preview.json().get("code") != "consulta"
        assert c.get("/api/erp-local/store-sales").status_code == 200
        assert erp.snapshot() == before
        # The worker goes on mirroring: it does not pass through the screen's routes.
        erp.command(
            "mirror-test",
            "stock.mirror",
            {"sku": "demo.aparelho", "physical": "6", "reserved": "1"},
        )


def test_off_in_the_demo_once_a_store_is_switched_and_when_told(tmp_path, monkeypatch):
    monkeypatch.delenv(consulta.ENV, raising=False)
    with app_client(tmp_path) as c:
        erp = c.app.state.erp
        assert c.get("/api/erp-local/state").json()["consulta"]["active"] is False
        mirrored(erp)
        monkeypatch.setenv(consulta.ENV, "0")
        assert change(c).status_code == 200
        monkeypatch.delenv(consulta.ENV)
        assert change(c).status_code == 409
        switch_a_store(erp)
        assert c.get("/api/erp-local/state").json()["consulta"]["active"] is False
        assert change(c).status_code == 200


def test_forced_on_even_in_the_demo(tmp_path, monkeypatch):
    monkeypatch.setenv(consulta.ENV, "1")
    with app_client(tmp_path) as c:
        assert c.get("/api/erp-local/state").json()["consulta"]["reason"] == "forçado"
        assert change(c).status_code == 409


def test_the_homologation_copy_keeps_accepting_changes(erp, tmp_path, monkeypatch):
    from app.erp_local import homolog

    monkeypatch.delenv(consulta.ENV, raising=False)
    mirrored(erp)
    with erp.connection() as db:
        assert consulta.active(db) is True
    target = tmp_path / "copy.sqlite3"
    assert homolog.seed(erp.database, target) is True
    with TestClient(create_app(target), base_url="http://127.0.0.1:8765") as c:
        assert c.get("/api/erp-local/state").json()["consulta"]["active"] is False
        assert change(c).status_code == 200


def test_the_screen_opens_with_the_real_base_shapes(tmp_path):
    """Stores registered only by name (adopted from DaVinci, Temu copied from Bling) and reads
    whose orders do not say if they are in the window: the page state still answers."""
    import json

    from app.erp_local import integration_registry

    with app_client(tmp_path) as c:
        erp = c.app.state.erp
        assert seed(c).status_code == 200
        c.get("/api/erp-local/state")  # every table exists
        with erp.connection() as db:
            db.execute(
                "INSERT INTO store_policies VALUES ('temu','barbosa','active',?,?)",
                (json.dumps({"name": "Temu Barbosa", "copied_from_bling": True}), "2026-11-18"),
            )
            columns = [r[1] for r in db.execute("PRAGMA table_info(native_order_reads)")]
            report = {
                "limit_per_account": 50,
                "accounts": [{"integration_id": "x", "platform": "temu", "orders": [{"id": "1"}]}],
            }
            values = {"data": json.dumps(report), "captured_at": "2026-11-18T10:00:00+00:00"}
            db.execute(
                f"INSERT INTO native_order_reads({','.join(columns)}) "  # noqa: S608
                f"VALUES ({','.join('?' * len(columns))})",
                [values.get(col, "leitura-" + col) for col in columns],
            )
        assert integration_registry.authorization_snapshot(db, {"name": "só nome"}) is None
        state = c.get("/api/erp-local/state")
        assert state.status_code == 200
