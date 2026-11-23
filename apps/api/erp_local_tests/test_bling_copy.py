"""Stores read from DaVinci's copy of Bling (Temu, ML Fioree): kept and compared like a store,
never an own order."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.erp_local import bling_copy_order_probe as probe
from app.erp_local import (
    bling_copy_orders,
    bling_coverage,
    native_ingest,
    parallel_check,
    recipient_capture,
    service_loop,
    store_sales,
)
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
    bling_copy_orders.validate(read_of(rows))


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
        bling_copy_orders.validate(read)


def test_unknown_store_is_never_read_ok():
    read = read_of([row("309312")])
    read["accounts"][0]["integration_id"] = "999"
    with pytest.raises(ERPError):
        bling_copy_orders.validate(read)
    refused = probe.envelope([probe.refused("999", START, END, NOW, "configuration_missing")], NOW)
    bling_copy_orders.validate(refused)


def test_temu_stores_enter_once_and_come_back_after_the_register(erp):
    assert sorted(service_loop.adopt_bling_copy_stores(erp)) == [
        "ml:Fioree",
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


FIOREE = "206368117"
ML_SALE = "2000013912345678"


def ml_row(numero="320001", sale=ML_SALE, **changes):
    return {**row(numero, sale), "total": "61.90", "totalprodutos": "49.90", **changes}


def read_fioree(erp, rows):
    def provider(*, channel, store, date_from, date_to):
        return read_of(rows, store, date_from, date_to)

    service_loop.adopt_bling_copy_stores(erp)
    return native_ingest.run_account(erp, "ml", FIOREE, provider, now=NOW)


def test_ml_fioree_is_read_from_the_copy_by_its_mercado_livre_number():
    account = read_of([ml_row(), ml_row("1", SALE)], store=FIOREE)["accounts"][0]
    assert account["platform"] == "ml" and [o["id"] for o in account["orders"]] == [ML_SALE]
    bling_copy_orders.validate(read_of([ml_row()], store=FIOREE))
    # Each store keeps its own channel and number: a Temu number or channel never passes as ML.
    for change in (
        lambda read: read["accounts"][0].update(platform="temu"),
        lambda read: read["accounts"][0]["orders"][0].update(id=SALE),
    ):
        read = read_of([ml_row()], store=FIOREE)
        change(read)
        with pytest.raises(ERPError):
            bling_copy_orders.validate(read)
    assert probe.copied("ml", FIOREE) and not probe.copied("temu", FIOREE)
    assert not probe.copied("ml", STORE) and not probe.copied("ml", "uuid-de-integracao")


def test_fioree_sale_keeps_blings_total_and_is_never_promoted(erp):
    assert read_fioree(erp, [ml_row()])["added"] == 1
    with erp.connection() as db:
        plan = native_ingest.promotion_plan(db, "ml", FIOREE)
        (sale,) = store_sales.listing(db)["items"]
        units = native_ingest._units(db, "ml", FIOREE)
        total = parallel_check.sale_total(db, "ml", FIOREE, ML_SALE, list(units[ML_SALE].values()))
    # Bling's total already has freight and coupon: no Mercado Livre shipment to wait for.
    # Blocked only for being a copy (and the buyer not read yet): items matched, total known.
    assert plan["ready"] == [] and set(plan["blocked"]) <= {"bling_copy", "recipient_missing"}
    assert "bling_copy" in plan["blocked"]
    assert sale["channel"] == "ml" and sale["store"] == "ML Fioree" and sale["total"] == "61.90"
    assert total == "61.90"
    assert not native_ingest.charged("ml", FIOREE) and native_ingest.charged("ml", "outra")


def test_fioree_buyer_comes_from_the_copy_and_other_ml_stores_never_ask_it(erp):
    read_fioree(erp, [ml_row()])

    def read(_sql):
        return [
            {
                "order_id": ML_SALE,
                "name": "Bruna Lima",
                "document": "529.982.247-25",
                "street": "Rua Exemplo",
                "number": "8",
                "complement": "",
                "district": "Centro",
                "city": "Belo Horizonte",
                "state": "MG",
                "postal_code": "30110-000",
            }
        ]

    reader = recipient_capture.bling_copy_reader(read)
    assert recipient_capture.read_due(erp, "ml", FIOREE, reader, now=NOW)["ready"] == 1
    assert reader(channel="ml", store="uuid-de-integracao", ids=[ML_SALE]) == {}
    with erp.connection() as db:
        found = recipient_capture.for_order(db, "ml", FIOREE, ML_SALE)
    assert found["ready"] and found["buyer"]["name"] == "Bruna Lima"


def test_coverage_finds_fioree_sales_by_their_number(erp):
    read_fioree(erp, [ml_row()])
    bling = [
        {
            "number": "320001",
            "store_number": ML_SALE,
            "bling_store": FIOREE,
            "store": None,
            "channel": None,
            "name": None,
            "day": "2026-11-17",
            "total": "61.90",
            "situation": "15",
        }
    ]
    with erp.connection() as db:
        accounts = {
            (r["channel"], r["store"]): r["last_state"]
            for r in db.execute("SELECT channel,store,last_state FROM ingest_accounts")
        }
        (store,) = bling_coverage.compare(db, bling, accounts)["stores"]
    assert (store["channel"], store["store"], store["name"]) == ("ml", FIOREE, "ML Fioree")
    assert store["found"] == 1 and store["missing"] == 0 and store["equal"] == 1


def test_the_round_reads_copied_stores_through_the_copy_reader(erp, monkeypatch):
    from app.erp_local import native_orders

    monkeypatch.setattr(native_orders, "import_read", lambda _erp, read: {"observations_added": 0})
    service_loop.adopt_bling_copy_stores(erp)
    with erp.connection() as db:
        db.execute(
            "INSERT INTO store_policies VALUES ('ml','uuid-nativa','identity_review','{}',?)",
            (erp.now(),),
        )
        db.execute(
            "INSERT INTO ingest_accounts(channel,store,enabled,authority,failures,updated_at) "
            "VALUES ('ml','uuid-nativa',1,'bling',0,?)",
            (erp.now(),),
        )
    asked = {}

    def provider_for(platform):
        def read(*, channel, store, date_from, date_to):
            asked[(channel, store)] = platform
            account = {"integration_id": store, "state": "read_ok", "orders": [], "has_more": False}
            return {"accounts": [account]}

        return read

    service_loop.once(erp, provider_for=provider_for, channels={"ml"})
    assert asked == {("ml", FIOREE): "bling_copy", ("ml", "uuid-nativa"): "ml"}
