"""The reading window closes at the store: what was read complete must survive what comes after."""

import asyncio
import json

import pytest

from app.erp_local import channel_recipients as cr
from app.erp_local import recipient_capture as rc
from app.erp_local import recipient_order_read as ror
from app.erp_local.core import ERPError

CHANNEL, STORE = "shopee", "loja-teste"
CPF = "52998224725"


def open_order(**address):
    return {
        "buyer_cpf_id": CPF,
        "recipient_address": {
            "name": "Ana Souza",
            "city": "Belo Horizonte",
            "district": "Centro",
            "state": "Minas Gerais",
            "zipcode": "30110-000",
            "full_address": (
                "Rua Exemplo, 20, Casa 2, Centro, Belo Horizonte, Minas Gerais, 30110000"
            ),
            **address,
        },
    }


def masked_order():
    return {
        "buyer_cpf_id": "****",
        "recipient_address": {
            "name": "A***",
            "full_address": "****",
            "city": "B***",
            "state": "M***",
            "zipcode": "****",
        },
    }


def observe(erp, external, status, stamp="2026-11-06T10:00:00+00:00"):
    with erp.connection() as db:
        db.execute("PRAGMA foreign_keys=OFF")
        columns = [r[1] for r in db.execute("PRAGMA table_info(native_order_observations)")]
        values = {
            "id": external,
            "channel": CHANNEL,
            "store": STORE,
            "external_id": external,
            "updated_at": stamp,
            "captured_at": stamp,
            "data": json.dumps({"status": status}),
        }
        db.execute(
            f"INSERT INTO native_order_observations({','.join(columns)}) "  # noqa: S608
            f"VALUES ({','.join('?' * len(columns))})",
            [values.get(c, "x") for c in columns],
        )


def stored(erp, external):
    with erp.connection() as db:
        return rc.for_order(db, CHANNEL, STORE, external)


def test_shopee_line_splits_into_street_number_and_complement():
    result = cr.from_shopee(open_order())
    assert result["ready"] is True, result["missing"]
    delivery = result["recipient"]
    assert (delivery["street"], delivery["number"], delivery["complement"]) == (
        "Rua Exemplo",
        "20",
        "Casa 2",
    )
    assert delivery["state"] == "MG" and delivery["ibge_code"] == "3106200"
    assert result["buyer"] == {"name": "Ana Souza", "document": CPF}


def test_shopee_without_number_is_explicit_and_two_letter_state_is_kept():
    result = cr.from_shopee(open_order(full_address="Rua Exemplo, Centro", state="mg"))
    assert result["recipient"]["number"] == "S/N" and result["recipient"]["state"] == "MG"


def test_masked_shopee_order_is_never_ready_and_keeps_no_mask():
    result = cr.from_shopee(masked_order())
    assert result["ready"] is False
    assert "*" not in json.dumps(result)


def test_complete_reading_survives_a_later_masked_one(erp):
    assert rc.capture(erp, CHANNEL, STORE, "A1", open_order())["ready"] is True
    later = rc.capture(erp, CHANNEL, STORE, "A1", masked_order(), status="SHIPPED")
    assert later["kept"] is True
    kept = stored(erp, "A1")
    assert kept["ready"] is True and kept["buyer"]["document"] == CPF


def test_incomplete_reading_can_become_complete_and_address_can_change(erp):
    assert rc.capture(erp, CHANNEL, STORE, "A2", masked_order())["ready"] is False
    assert stored(erp, "A2")["ready"] is False
    rc.capture(erp, CHANNEL, STORE, "A2", open_order())
    rc.capture(erp, CHANNEL, STORE, "A2", open_order(district="Savassi"))
    assert stored(erp, "A2")["recipient"]["district"] == "Savassi"


def test_only_the_projection_is_stored(erp):
    raw = open_order()
    raw["recipient_address"]["phone"] = "31999990000"
    raw["extra_field"] = "nao-guardar"
    rc.capture(erp, CHANNEL, STORE, "A3", raw)
    with erp.connection() as db:
        data = db.execute("SELECT data FROM native_order_recipients").fetchone()[0]
    assert "nao-guardar" not in data and "31999990000" not in data


def test_due_follows_the_status_window_of_each_channel(erp):
    observe(erp, "open", "READY_TO_SHIP")
    observe(erp, "late", "SHIPPED")
    observe(erp, "gone", "CANCELLED")
    observe(erp, "done", "PROCESSED")
    rc.capture(erp, CHANNEL, STORE, "done", open_order())
    with erp.connection() as db:
        assert rc.due(db, CHANNEL, STORE) == {"open": "READY_TO_SHIP"}


def test_read_due_captures_and_asks_only_for_what_is_due(erp):
    observe(erp, "B1", "READY_TO_SHIP")
    observe(erp, "B2", "PROCESSED")
    asked = []

    def reader(*, channel, store, ids):
        asked.append(ids)
        return {"B1": open_order()}

    report = rc.read_due(erp, CHANNEL, STORE, reader)
    assert asked == [["B1", "B2"]] and report == {"state": "read", "due": 2, "ready": 1}
    assert rc.read_due(erp, CHANNEL, STORE, reader)["due"] == 1
    assert asked[-1] == ["B2"]


def test_channel_without_reader_is_reported_not_read(erp):
    def reader(**_):
        raise AssertionError("não deveria ler")

    assert rc.read_due(erp, "amazon", STORE, reader)["state"] == "not_supported"


def test_summary_has_counts_only(erp):
    rc.capture(erp, CHANNEL, STORE, "C1", open_order())
    rc.capture(erp, CHANNEL, STORE, "C2", masked_order())
    with erp.connection() as db:
        report = rc.summary(db)
    entry = report[f"{CHANNEL}:{STORE}"]
    assert entry["ready"] == 1 and entry["incomplete"] == 1 and entry["missing"]["nome"] == 1
    assert CPF not in json.dumps(report) and "Ana" not in json.dumps(report)


def test_contact_is_created_once_and_follows_the_new_address(erp):
    rc.capture(erp, CHANNEL, STORE, "D1", open_order())
    first = rc.contact_for(erp, stored(erp, "D1"))
    assert rc.contact_for(erp, stored(erp, "D1")) == first
    rc.capture(erp, CHANNEL, STORE, "D2", open_order(district="Savassi"))
    assert rc.contact_for(erp, stored(erp, "D2")) == first
    with erp.connection() as db:
        rows = db.execute("SELECT data FROM own_contacts").fetchall()
    assert len(rows) == 1 and json.loads(rows[0][0])["district"] == "Savassi"


def test_reading_buyers_over_ssh_stays_closed_here(monkeypatch):
    monkeypatch.delenv(rc.CAPTURE_ENV, raising=False)
    with pytest.raises(ERPError, match="fechada"):
        rc.ssh_reader()


class Reply:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def json(self):
        return self.body


class Client:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    async def get(self, url, *, headers=None, params=None):
        self.calls.append((url, params))
        return self.routes[url.rsplit("/api", 1)[-1] if "/api/" in url else url]


def test_shopee_reader_asks_named_orders_and_returns_only_needed_fields(monkeypatch):
    monkeypatch.setattr(ror, "shopee_query", lambda _p, _c, _s, query: query, raising=True)
    monkeypatch.setattr(ror, "BASE", "https://x", raising=False)
    order = {**open_order(), "order_sn": "S1", "buyer_username": "nao-sai", "item_list": []}
    client = Client(
        {"/v2/order/get_order_detail": Reply({"error": "", "response": {"order_list": [order]}})}
    )
    client.routes["https://x/api/v2/order/get_order_detail"] = client.routes[
        "/v2/order/get_order_detail"
    ]
    result = asyncio.run(ror.recipients_shopee(client, {}, None, ["S1"]))
    assert set(result["S1"]) == {"buyer_cpf_id", "recipient_address"}
    assert client.calls[0][1]["response_optional_fields"] == "recipient_address,buyer_cpf_id"


def test_shopee_reader_skips_a_refused_batch(monkeypatch):
    monkeypatch.setattr(ror, "shopee_query", lambda _p, _c, _s, query: query, raising=True)
    monkeypatch.setattr(ror, "BASE", "https://x", raising=False)
    client = Client({"/v2/order/get_order_detail": Reply({"error": "error_auth"})})
    assert asyncio.run(ror.recipients_shopee(client, {}, None, ["S1"])) == {}
