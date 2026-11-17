"""Temu read from DaVinci's copy of Bling: kept and compared like a store, never an own order."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.erp_local import (
    bling_coverage,
    native_ingest,
    recipient_capture,
    service_loop,
    store_sales,
    temu_orders,
)
from app.erp_local import temu_order_probe as probe
from app.erp_local.core import ERPError

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
START, END = NOW - timedelta(hours=24), NOW - timedelta(minutes=2)
STORE = "205925390"
SALE = "PO-076-12345678901234567"


def row(numero, sale=SALE, *, index=0, sku="demo.aparelho", situation="15", hours=1):
    return {
        "numero": numero,
        "numeroloja": sale,
        "situacao": situation,
        "data": datetime(2026, 11, 17, 3, tzinfo=UTC),
        "total": "45.80",
        "totalprodutos": "35.80",
        "updated_at": NOW - timedelta(hours=hours),
        "item_index": index,
        "item_codigo": sku,
        "item_quantidade": 1,
        "itemvalor": "35.8",
        "item_desconto": 0,
    }


def read_of(rows, store=STORE, start=START, end=END):
    return probe.envelope([probe.project(rows, store, start, end, NOW)], NOW)


def test_each_sale_once_under_its_first_bling_number():
    rows = [
        row("309312", index=0),
        row("309312", index=1, sku="demo.fone"),
        # A resend Bling made later under the same Temu number is Bling's, not the sale.
        row("309400", index=0, sku="outro", hours=0.5),
        row("309500", "PO-076-99999999999999999", situation="12"),
        row("1", "2000009"),
    ]
    account = read_of(rows)["accounts"][0]
    first, cancelled = sorted(account["orders"], key=lambda o: o["id"])
    assert first["id"] == SALE and first["bling_number"] == "309312"
    assert [i["seller_sku"] for i in first["items"]] == ["demo.aparelho", "demo.fone"]
    assert first["items"][0]["unit_price"] == {"amount": "35.80", "currency": "BRL"}
    assert first["total"] == {"amount": "45.80", "currency": "BRL"}
    assert (
        first["status"] == "BLING_COPY"
        and first["updated_at"] == (NOW - timedelta(hours=0.5)).isoformat()
    )
    assert cancelled["status"] == "CANCELLED" and account["has_more"] is False
    temu_orders.validate(read_of(rows))


@pytest.mark.parametrize(
    "change",
    [
        lambda o: o.update(extra=1),
        lambda o: o.update(updated_at=(NOW + timedelta(hours=1)).isoformat()),
        lambda o: o.update(status="CANCELLED"),
        lambda o: o.update(id="2000009"),
        lambda o: o["items"][0].update(quantity="1"),
        lambda o: o["total"].update(amount="45.8"),
    ],
)
def test_anything_outside_the_projection_is_refused(change):
    read = read_of([row("309312")])
    change(read["accounts"][0]["orders"][0])
    with pytest.raises(ERPError):
        temu_orders.validate(read)


def test_unknown_store_is_never_read_ok():
    read = read_of([row("309312")], store="999")
    with pytest.raises(ERPError):
        temu_orders.validate(read)
    refused = probe.envelope([probe.refused("999", START, END, NOW, "configuration_missing")], NOW)
    temu_orders.validate(refused)


def test_temu_stores_enter_once_and_come_back_after_the_register(erp):
    assert sorted(service_loop.adopt_bling_copy_stores(erp)) == [
        "temu:Atv",
        "temu:Barbosa",
        "temu:Eron",
        "temu:JLAS",
    ]
    assert service_loop.adopt_bling_copy_stores(erp) == []
    with erp.connection() as db:
        db.execute("UPDATE store_policies SET status='inactive' WHERE channel='temu'")
    service_loop.adopt_bling_copy_stores(erp)
    with erp.connection() as db:
        states = {
            r[0] for r in db.execute("SELECT status FROM store_policies WHERE channel='temu'")
        }
        enabled = db.execute(
            "SELECT count(*) FROM ingest_accounts WHERE channel='temu' AND enabled=1"
        ).fetchone()[0]
    assert states == {"identity_review"} and enabled == 4


def read_temu(erp, rows):
    def provider(*, channel, store, date_from, date_to):
        return read_of(rows, store, date_from, date_to)

    service_loop.adopt_bling_copy_stores(erp)
    return native_ingest.run_account(erp, "temu", STORE, provider, now=NOW)


def test_read_sale_is_listed_once_with_its_item_and_is_never_promoted(erp):
    assert read_temu(erp, [row("309312")])["added"] == 1
    copy = {"store_number": SALE, "number": "309312", "situation": "15", "store": STORE}
    with erp.connection() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS reference_rows (kind TEXT, id TEXT, data TEXT, search TEXT)"
        )
        db.execute("INSERT INTO reference_rows VALUES ('orders','9',?,'')", (json.dumps(copy),))
        (sale,) = store_sales.listing(db)["items"]
        plan = native_ingest.promotion_plan(db, "temu", STORE)
    assert sale["channel"] == "temu" and sale["store"] == "Temu Barbosa"
    assert sale["status"] == "Cópia do Bling" and sale["total"] == "45.80"
    assert store_sales.REASONS["bling_copy"] in sale["blocked"]
    assert not any("vínculo" in reason for reason in sale["blocked"])
    assert plan["ready"] == [] and plan["blocked"]["bling_copy"] == 1
    with erp.connection() as db:
        db.execute("UPDATE ingest_accounts SET authority='own' WHERE channel='temu'")
    with pytest.raises(ERPError) as refused:
        native_ingest.promote(erp, "temu", STORE, SALE, depot="principal")
    assert refused.value.code == "ingest_bling_copy"


def test_buyer_comes_from_the_bling_copy_by_temu_number(erp):
    read_temu(erp, [row("309312")])
    asked = []

    def read(sql):
        asked.append(sql)
        return [
            {
                "order_id": SALE,
                "name": "Ana Souza",
                "document": "529.982.247-25",
                "street": "Rua Exemplo",
                "number": "20",
                "complement": "",
                "district": "Centro",
                "city": "Belo Horizonte",
                "state": "MG",
                "postal_code": "30110-000",
            }
        ]

    reader = recipient_capture.bling_copy_reader(read)
    report = recipient_capture.read_due(erp, "temu", STORE, reader, now=NOW)
    assert report == {"state": "read", "due": 1, "ready": 1} and f"'{SALE}'" in asked[0]
    assert reader(channel="temu", store=STORE, ids=["x'); drop"]) == {}
    with erp.connection() as db:
        assert recipient_capture.for_order(db, "temu", STORE, SALE)["buyer"]["name"] == "Ana Souza"


def test_coverage_finds_the_temu_sales_the_erp_read(erp):
    read_temu(erp, [row("309312")])
    bling = [
        {
            "number": "309312",
            "store_number": SALE,
            "bling_store": STORE,
            "store": None,
            "channel": None,
            "name": None,
            "day": "2026-11-17",
            "total": "45.80",
            "situation": "15",
        }
    ]
    with erp.connection() as db:
        accounts = {
            (r["channel"], r["store"]): r["last_state"]
            for r in db.execute("SELECT channel,store,last_state FROM ingest_accounts")
        }
        (store,) = bling_coverage.compare(db, bling, accounts)["stores"]
    assert store["channel"] == "temu" and store["found"] == 1 and store["missing"] == 0
    assert store["name"] == "Temu Barbosa"
    assert store["equal"] == 1
