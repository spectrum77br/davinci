"""Behind the gate, what someone may do comes from their profile. Unknown people see nothing."""

import pytest
from fastapi.testclient import TestClient

from app.erp_local import access
from app.erp_local.api import create_app
from app.erp_local.core import ERPError

HEADERS = {"x-erp-local": "1"}
OWNER, SELLER, DRIVER = "dono@x.com", "ana@x.com", "ze@x.com"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ERP_TRUST_PROXY", "1")
    monkeypatch.setenv(access.ADMINS_ENV, OWNER)
    return TestClient(create_app(tmp_path / "api.sqlite3"), base_url="http://127.0.0.1:8765")


def send(client, actor, key, kind, payload):
    return client.post(
        "/api/erp-local/commands",
        headers={**HEADERS, "x-erp-actor": actor},
        json={"operation_key": key, "kind": kind, "payload": payload},
    )


def grant(client, actor, profile):
    reply = send(
        client,
        OWNER,
        "grant-" + actor,
        "access.user.save",
        {"actor": actor, "profile": profile, "active": True},
    )
    assert reply.status_code == 200, reply.text


def test_every_known_command_belongs_to_a_module():
    known = ["order.save", "purchase.create", "fiscal.invoice.prepare", "finance.pay", "kit"]
    assert [access.area_of(k) for k in known] == [
        "vendas",
        "compras",
        "fiscal",
        "financeiro",
        "cadastros",
    ]
    assert access.area_of("algo.novo") is None and access.area_of("access.user.save") is None


def test_unknown_person_sees_nothing(client):
    assert client.get("/api/erp-local/state").status_code == 403
    reply = client.get("/api/erp-local/state", headers={"x-erp-actor": "fora@x.com"})
    assert reply.status_code == 403 and reply.json()["code"] == "access_unknown"
    assert send(client, "fora@x.com", "k1", "category.save", {}).status_code == 403


def test_a_command_outside_the_profile_is_refused_and_inside_reaches_the_rules(client):
    grant(client, SELLER, "vendas")
    denied = send(client, SELLER, "k2", "finance.entry.create", {})
    assert denied.status_code == 403 and denied.json()["code"] == "access_denied"
    inside = send(client, SELLER, "k3", "category.save", {"name": "Teste"})
    assert inside.json().get("code") != "access_denied"


def test_commands_without_a_module_and_grants_are_for_administrators(client):
    grant(client, SELLER, "vendas")
    reply = send(
        client,
        SELLER,
        "k4",
        "access.user.save",
        {"actor": SELLER, "profile": "administrador", "active": True},
    )
    assert reply.status_code == 403


def test_page_data_keeps_its_shape_without_the_blocked_modules(client):
    grant(client, DRIVER, "expedicao")
    owner = client.get("/api/erp-local/state", headers={"x-erp-actor": OWNER}).json()
    assert owner["me"]["admin"] is True and owner["access"]["profiles"]
    driver = client.get("/api/erp-local/state", headers={"x-erp-actor": DRIVER}).json()
    assert driver["me"]["areas"] == ["expedicao"]
    assert set(driver["finance"]) == set(owner["finance"])
    assert all(not v for v in driver["finance"].values() if isinstance(v, list))
    assert driver["access"]["users"] == [] and driver["logistics"] == owner["logistics"]


def test_a_person_turned_inactive_loses_access(client):
    grant(client, SELLER, "vendas")
    send(
        client,
        OWNER,
        "off",
        "access.user.save",
        {"actor": SELLER, "profile": "vendas", "active": False},
    )
    assert client.get("/api/erp-local/state", headers={"x-erp-actor": SELLER}).status_code == 403


def test_uploads_follow_the_module_of_their_route(client):
    grant(client, DRIVER, "expedicao")
    reply = client.post(
        "/api/erp-local/fiscal/xml", headers={**HEADERS, "x-erp-actor": DRIVER}, content=b"<a/>"
    )
    assert reply.status_code == 403
    other = client.post(
        "/api/erp-local/reference-refresh", headers={**HEADERS, "x-erp-actor": DRIVER}
    )
    assert other.status_code == 403


def test_custom_profile_only_takes_known_modules_and_builtins_stay_fixed(erp):
    with erp.connection() as db:
        with pytest.raises(ERPError, match="desconhecido"):
            access.apply(erp, db, "access.profile.save", {"name": "x", "areas": ["nada"]})
        with pytest.raises(ERPError, match="fixo"):
            access.apply(erp, db, "access.profile.save", {"name": "vendas", "areas": []})
        access.apply(erp, db, "access.profile.save", {"name": "caixa", "areas": ["vendas"]})
        assert access.profiles(db)["caixa"] == ["vendas"]


def test_outside_the_gate_nothing_changes(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_TRUST_PROXY", raising=False)
    client = TestClient(create_app(tmp_path / "a.sqlite3"), base_url="http://127.0.0.1:8765")
    assert client.get("/api/erp-local/state").status_code == 200
