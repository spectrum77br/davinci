"""Reception rounds with a fake provider: no store is reached and nothing is promoted."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from test_core import balance, run
from test_native_orders import ACCOUNT, known_account, report

from app.erp_local import native_ingest, native_orders
from app.erp_local.core import ERPError

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
CHANNEL = "amazon"


def fake_read(state="read_ok", orders=(), has_more=False, retry_after=None):
    account = {
        "integration_id": ACCOUNT,
        "state": state,
        "orders": list(orders),
        "has_more": has_more,
    }
    if retry_after:
        account["retry_after"] = retry_after
    return {"accounts": [account]}


class Provider:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, *, channel, store, date_from, date_to):
        self.calls.append((date_from, date_to))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def importer(_erp, read):
    return {"observations_added": len(read["accounts"][0]["orders"])}


@pytest.fixture
def account(erp):
    known_account(erp)
    run(erp, "ingest.account.set", {"channel": CHANNEL, "store": ACCOUNT, "enabled": True})
    return erp


def state_of(erp):
    with erp.connection() as db:
        return dict(native_ingest._account(db, CHANNEL, ACCOUNT))


def go(erp, provider, now=NOW):
    return native_ingest.run_account(erp, CHANNEL, ACCOUNT, provider, now=now, importer=importer)


def test_accounts_stay_off_until_enabled(erp):
    known_account(erp)
    with pytest.raises(ERPError):
        native_ingest.run_account(erp, CHANNEL, ACCOUNT, Provider())
    run(erp, "ingest.account.set", {"channel": CHANNEL, "store": ACCOUNT, "enabled": False})
    provider = Provider()
    assert native_ingest.run_account(erp, CHANNEL, ACCOUNT, provider)["state"] == "disabled"
    assert provider.calls == []
    with pytest.raises(ERPError, match="registro próprio"):
        run(erp, "ingest.account.set", {"channel": CHANNEL, "store": "outra", "enabled": True})
    with erp.connection() as db:
        assert native_ingest.snapshot(db)["accounts"][0]["authority"] == "bling"


def test_complete_window_moves_cursor_and_next_window_overlaps(account):
    provider = Provider(
        fake_read(orders=[{"updated_at": "2026-11-06T11:00:00+00:00"}]), fake_read()
    )
    assert go(account, provider)["state"] == "complete"
    state = state_of(account)
    end = NOW - native_ingest.SAFETY_LAG
    assert state["cursor"] == end.isoformat() and state["last_ok_at"] == NOW.isoformat()
    assert provider.calls[0] == (NOW - native_ingest.FIRST_WINDOW, end)
    later = NOW + timedelta(minutes=5)
    go(account, provider, later)
    assert provider.calls[1][0] == end - native_ingest.OVERLAP


def test_paging_narrows_by_last_update_and_failure_keeps_the_cursor(account):
    first = NOW - timedelta(hours=3)
    provider = Provider(
        fake_read(orders=[{"updated_at": first.isoformat()}], has_more=True),
        fake_read(state="provider_unavailable"),
    )
    result = go(account, provider)
    assert result["state"] == "provider_unavailable" and result["observed"] == 1
    state = state_of(account)
    assert state["cursor"] is None and state["failures"] == 1
    assert state["window_from"] == first.isoformat()
    assert provider.calls[1][0] == first


def test_failed_round_waits_with_growing_backoff_and_resumes_the_same_window(account):
    provider = Provider(
        fake_read(state="provider_unavailable"), fake_read(state="rate_limited"), fake_read()
    )
    go(account, provider)
    state = state_of(account)
    window_to = state["window_to"]
    assert state["next_attempt_at"] == (NOW + timedelta(seconds=60)).isoformat()
    assert go(account, provider, NOW + timedelta(seconds=10))["state"] == "waiting"
    assert len(provider.calls) == 1
    go(account, provider, NOW + timedelta(seconds=61))
    state = state_of(account)
    assert state["failures"] == 2 and state["window_to"] == window_to
    assert state["next_attempt_at"] == (NOW + timedelta(seconds=61 + 120)).isoformat()
    assert go(account, provider, NOW + timedelta(seconds=200))["state"] == "complete"
    state = state_of(account)
    assert state["failures"] == 0 and state["window_to"] is None and state["cursor"] == window_to


@pytest.mark.parametrize("state", sorted(native_ingest.NEEDS_PERSON))
def test_authorization_problems_stop_retries_until_someone_acts(account, state):
    provider = Provider(fake_read(state=state))
    assert go(account, provider)["state"] == state
    assert state_of(account)["enabled"] == 0
    assert go(account, provider, NOW + timedelta(days=1))["state"] == "disabled"
    assert len(provider.calls) == 1


def test_stuck_pages_and_broken_replies_never_advance(account):
    provider = Provider(fake_read(orders=[], has_more=True))
    assert go(account, provider)["state"] == "stuck_page"
    assert state_of(account)["cursor"] is None
    provider = Provider(ValueError("resposta inválida"))
    assert go(account, provider, NOW + timedelta(hours=2))["state"] == "invalid_response"
    assert state_of(account)["cursor"] is None and state_of(account)["lease_until"] is None


def test_unproven_window_is_kept_as_observation_but_does_not_move_the_cursor(account):
    provider = Provider(fake_read(orders=[{"updated_at": NOW.isoformat()}], has_more=None))
    result = go(account, provider)
    assert result["state"] == "window_unproven" and result["added"] == 1
    assert state_of(account)["cursor"] is None


def test_long_window_continues_next_round_with_the_same_end(account, monkeypatch):
    monkeypatch.setattr(native_ingest, "MAX_READS", 2)
    first, second = NOW - timedelta(hours=10), NOW - timedelta(hours=9)
    provider = Provider(
        fake_read(orders=[{"updated_at": first.isoformat()}], has_more=True),
        fake_read(orders=[{"updated_at": second.isoformat()}], has_more=True),
        fake_read(),
    )
    assert go(account, provider)["state"] == "partial"
    state = state_of(account)
    assert state["window_from"] == second.isoformat() and state["cursor"] is None
    assert state["failures"] == 0
    assert go(account, provider, NOW + timedelta(minutes=1))["state"] == "complete"
    assert provider.calls[2] == (second, NOW - native_ingest.SAFETY_LAG)
    assert state_of(account)["cursor"] == (NOW - native_ingest.SAFETY_LAG).isoformat()


def test_lease_blocks_a_second_worker_and_expires(account):
    assert native_ingest._take_lease(account, CHANNEL, ACCOUNT, NOW) is not None
    provider = Provider(fake_read())
    assert go(account, provider, NOW + timedelta(minutes=1))["state"] == "busy"
    assert provider.calls == []
    assert (
        go(account, provider, NOW + native_ingest.LEASE + timedelta(seconds=1))["state"]
        == "complete"
    )


def test_lookback_reads_recent_days_daily_without_moving_the_cursor(account):
    go(account, Provider(fake_read()))
    cursor = state_of(account)["cursor"]
    days = native_ingest.FIRST_LOOKBACK_WINDOW.days + 1
    provider = Provider(fake_read(orders=[{"updated_at": NOW.isoformat()}]), *[fake_read()] * days)
    run_lookback = native_ingest.run_lookback
    assert (
        run_lookback(account, CHANNEL, ACCOUNT, provider, now=NOW, importer=importer)["added"] == 1
    )
    # The first re-read covers a month, one day per read; the daily ones after it, a week.
    assert provider.calls[0][0] == NOW - native_ingest.FIRST_LOOKBACK_WINDOW
    assert all(b - a <= native_ingest.MAX_WINDOW for a, b in provider.calls)
    assert state_of(account)["cursor"] == cursor
    early = NOW + timedelta(hours=1)
    assert run_lookback(account, CHANNEL, ACCOUNT, provider, now=early)["state"] == "not_due"
    later, daily = NOW + timedelta(days=1), Provider(*[fake_read()] * 8)
    run_lookback(account, CHANNEL, ACCOUNT, daily, now=later, importer=importer)
    assert daily.calls[0][0] == later - native_ingest.LOOKBACK_WINDOW


def test_one_failing_account_does_not_stop_the_pass(account):
    def broken(**_kwargs):
        raise OSError("sem rota")

    result = native_ingest.run_due(account, {CHANNEL: broken}, now=NOW)
    assert result[(CHANNEL, ACCOUNT)]["state"] == "invalid_response"
    assert native_ingest.run_due(account, {}, now=NOW) == {}


def test_real_projection_imports_once_and_never_reserves(account):
    before = balance(account)
    moment = datetime.now(UTC)
    read = report()
    read["accounts"][0]["has_more"] = False
    provider = Provider(read, read)
    first = native_ingest.run_account(account, CHANNEL, ACCOUNT, provider, now=moment)
    assert first == {"state": "complete", "reads": 1, "observed": 1, "added": 1}
    later = moment + timedelta(minutes=5)
    second = native_ingest.run_account(account, CHANNEL, ACCOUNT, provider, now=later)
    assert second["state"] == "complete" and second["added"] == 0
    with account.connection() as db:
        assert db.execute("SELECT count(*) FROM native_order_observations").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM documents WHERE kind='order'").fetchone()[0] == 0
    assert balance(account) == before


def unlinked_report():
    """Seller code outside the catalog, so nothing links the item."""
    read = report()
    read["accounts"][0]["orders"][0]["items"][0]["seller_sku"] = "FORA-DO-CATALOGO"
    return read


def test_promotion_plan_only_reports_and_flags_what_blocks(account):
    read = unlinked_report()
    native_orders.import_read(account, read)
    before = balance(account)
    with account.connection() as db:
        plan = native_ingest.promotion_plan(db, CHANNEL, ACCOUNT)
        assert db.execute("SELECT count(*) FROM documents WHERE kind='order'").fetchone()[0] == 0
    assert plan["observed"] == 1 and plan["ready"] == []
    assert plan["blocked"]["item_missing"] == 1
    assert balance(account) == before


def test_long_gap_is_read_one_day_per_round_until_it_catches_up(account):
    old = NOW - timedelta(days=3)
    native_ingest._save(account, CHANNEL, ACCOUNT, cursor=old.isoformat())
    provider = Provider(fake_read(), fake_read())
    go(account, provider)
    first_end = old - native_ingest.OVERLAP + native_ingest.MAX_WINDOW
    assert provider.calls[0] == (old - native_ingest.OVERLAP, first_end)
    assert state_of(account)["cursor"] == first_end.isoformat()
    go(account, provider, NOW + timedelta(minutes=1))
    assert provider.calls[1][0] == first_end - native_ingest.OVERLAP


def test_promotion_stays_closed_until_both_switches_are_on(account, monkeypatch):
    native_orders.import_read(account, report())
    external = report()["accounts"][0]["orders"][0]["id"]
    with pytest.raises(ERPError, match="Bling segue"):
        native_ingest.promote(account, CHANNEL, ACCOUNT, external, depot="geral")
    monkeypatch.delenv("ERP_ALLOW_AUTHORITY_SWITCH", raising=False)
    with pytest.raises(ERPError, match="fechada"):
        native_ingest.set_authority(account, CHANNEL, ACCOUNT, "own")
    assert state_of(account)["authority"] == "bling"


def test_promotion_needs_an_exact_link_and_reserves_once(account, monkeypatch):
    monkeypatch.setenv("ERP_ALLOW_AUTHORITY_SWITCH", "1")
    native_ingest.set_authority(account, CHANNEL, ACCOUNT, "own")
    read = unlinked_report()
    native_orders.import_read(account, read)
    external = read["accounts"][0]["orders"][0]["id"]
    with pytest.raises(ERPError, match="vínculo"):
        native_ingest.promote(account, CHANNEL, ACCOUNT, external, depot="geral")
    item = read["accounts"][0]["orders"][0]["items"][0]
    with account.connection() as db:
        columns = [r[1] for r in db.execute("PRAGMA table_info(stock_routes)")]
        values = {
            "channel": CHANNEL,
            "store": ACCOUNT,
            "external_id": item["seller_sku"],
            "variation_id": item["asin"],
            "sku": "demo.aparelho",
            "depot": "geral",
            "active": 1,
            "enabled": 1,
            "status": "active",
            "updated_at": NOW.isoformat(),
            "created_at": NOW.isoformat(),
        }
        db.execute(
            f"INSERT INTO stock_routes({','.join(columns)}) "  # noqa: S608
            f"VALUES ({','.join('?' * len(columns))})",
            [values.get(c, "") for c in columns],
        )
    with pytest.raises(ERPError, match="Comprador incompleto"):
        native_ingest.promote(account, CHANNEL, ACCOUNT, external, depot="geral")
    run(
        account,
        "recipient.capture",
        {
            "channel": CHANNEL,
            "store": ACCOUNT,
            "external_id": external,
            "read_status": "Unshipped",
            "captured_at": NOW.isoformat(),
            "projection": {
                "ready": True,
                "missing": [],
                "buyer": {"name": "Ana Souza", "document": "52998224725"},
                "recipient": {
                    "name": "Bia Souza",
                    "street": "Rua Exemplo",
                    "number": "20",
                    "district": "Centro",
                    "city": "Belo Horizonte",
                    "state": "MG",
                    "postal_code": "30110000",
                    "ibge_code": "3106200",
                },
            },
        },
    )
    before = balance(account)
    created = native_ingest.promote(account, CHANNEL, ACCOUNT, external, depot="geral")
    assert created["id"] and balance(account) != before
    during = balance(account)
    again = native_ingest.promote(account, CHANNEL, ACCOUNT, external, depot="geral")
    assert again["replayed"] is True and balance(account) == during


def _money(amount):
    return {"amount": amount, "currency": "BRL"}


def observe_orders(erp, channel, store, *orders):
    with erp.connection() as db:
        db.execute("PRAGMA foreign_keys=OFF")
        columns = [r[1] for r in db.execute("PRAGMA table_info(native_order_observations)")]
        for order in orders:
            stamp = order.get("updated_at") or NOW.isoformat()
            values = {
                "id": f"{order['id']}-{stamp}",
                "channel": channel,
                "store": store,
                "external_id": order["id"],
                "updated_at": stamp,
                "captured_at": stamp,
                "data": json.dumps(order),
            }
            db.execute(
                f"INSERT INTO native_order_observations({','.join(columns)}) "  # noqa: S608
                f"VALUES ({','.join('?' * len(columns))})",
                [values.get(c, "x") for c in columns],
            )
            if channel == "ml" and order.get("_freight") is not False:
                # Mercado Livre's freight comes from the shipment, read apart from the order.
                key = str(order.get("pack_id") or order["id"])
                cost = order.get("_freight") or "0"
                db.execute(
                    "INSERT OR REPLACE INTO store_tracking VALUES ('ml',?,?,?,?)",
                    (store, key, json.dumps({"status": "ready_to_ship", "cost": cost}), stamp),
                )


def sale(identifier, *, status="paid", pack=None, sku="demo.aparelho", created=None):
    return {
        "id": identifier,
        "status": status,
        "pack_id": pack,
        "created_at": (created or NOW - timedelta(hours=2)).isoformat(),
        "coupon": _money("0"),
        "items": [
            {
                "line_id": f"{identifier}:1",
                "listing_id": "L" + identifier,
                "variation_id": None,
                "seller_sku": sku,
                "asin": None,
                "quantity": 1,
                "unit_price": _money("10.00"),
            }
        ],
    }


BUYER = {
    "ready": True,
    "missing": [],
    "buyer": {"name": "Ana Souza", "document": "52998224725"},
    "recipient": {
        "name": "Ana Souza",
        "street": "Rua Exemplo",
        "number": "20",
        "district": "Centro",
        "city": "Belo Horizonte",
        "state": "MG",
        "postal_code": "30110000",
        "ibge_code": "3106200",
    },
}
ML_STORE = "conta-ml"


def capture_buyer(erp, external):
    run(
        erp,
        "recipient.capture",
        {
            "channel": "ml",
            "store": ML_STORE,
            "external_id": external,
            "read_status": "paid",
            "captured_at": NOW.isoformat(),
            "projection": BUYER,
        },
    )


@pytest.fixture
def ml_account(erp, monkeypatch):
    with erp.connection() as db:
        columns = [r[1] for r in db.execute("PRAGMA table_info(ingest_accounts)")]
        values = {
            "channel": "ml",
            "store": ML_STORE,
            "enabled": 1,
            "authority": "own",
            "cursor": NOW.isoformat(),
            "failures": 0,
            "updated_at": NOW.isoformat(),
        }
        db.execute(
            f"INSERT INTO ingest_accounts({','.join(columns)}) "  # noqa: S608
            f"VALUES ({','.join('?' * len(columns))})",
            [values.get(c) for c in columns],
        )
    return erp


def plan_of(erp, channel="ml", store=ML_STORE):
    with erp.connection() as db:
        return native_ingest.promotion_plan(db, channel, store)


def test_cart_is_one_sale_under_its_own_number_with_all_items(ml_account):
    observe_orders(ml_account, "ml", ML_STORE, sale("A1", pack="P1"), sale("A2", pack="P1"))
    capture_buyer(ml_account, "A1")
    plan = plan_of(ml_account)
    assert plan["ready"] == ["P1"] and plan["observed"] == 2 and plan["sales"] == 1
    created = native_ingest.promote(ml_account, "ml", ML_STORE, "A2", depot="geral")
    with ml_account.connection() as db:
        _, data = ml_account.document(db, created["id"], "order")
    assert data["external_id"] == "P1" and len(data["items"]) == 2
    again = native_ingest.promote(ml_account, "ml", ML_STORE, "P1", depot="geral")
    assert again["replayed"] is True


def test_cart_of_one_order_is_not_held_for_review(ml_account):
    observe_orders(ml_account, "ml", ML_STORE, sale("A1", pack="P1"))
    capture_buyer(ml_account, "A1")
    assert plan_of(ml_account)["ready"] == ["P1"]


def test_cart_waits_until_the_reading_moved_past_its_newest_order(ml_account):
    recent = NOW + timedelta(minutes=5)
    observe_orders(ml_account, "ml", ML_STORE, sale("A1", pack="P1", created=recent))
    capture_buyer(ml_account, "A1")
    assert plan_of(ml_account)["blocked"] == {"cart_waiting": 1}
    with pytest.raises(ERPError, match="Carrinho"):
        native_ingest.promote(ml_account, "ml", ML_STORE, "P1", depot="geral")


def test_cancelled_order_of_a_cart_leaves_out_only_its_items(ml_account):
    observe_orders(
        ml_account,
        "ml",
        ML_STORE,
        sale("A1", pack="P1"),
        sale("A2", pack="P1", status="cancelled"),
    )
    capture_buyer(ml_account, "A1")
    created = native_ingest.promote(ml_account, "ml", ML_STORE, "P1", depot="geral")
    with ml_account.connection() as db:
        _, data = ml_account.document(db, created["id"], "order")
    assert len(data["items"]) == 1


@pytest.mark.parametrize(
    ("channel", "status", "open_"),
    [
        ("shopee", "UNPAID", False),
        ("shopee", "SHIPPED", False),
        ("shopee", "READY_TO_SHIP", True),
        ("shopee", "PROCESSED", True),
        ("ml", "paid", True),
        ("tiktok", "UNPAID", False),
        ("tiktok", "AWAITING_SHIPMENT", True),
        ("amazon", "UNSHIPPED", True),
        ("amazon", "PENDING", False),
        ("magalu", "approved", True),
        ("magalu", "finished", False),
    ],
)
def test_only_what_the_store_released_becomes_a_sale(erp, channel, status, open_):
    with erp.connection() as db:
        columns = [r[1] for r in db.execute("PRAGMA table_info(ingest_accounts)")]
        values = {
            "channel": channel,
            "store": "conta",
            "enabled": 1,
            "authority": "own",
            "failures": 0,
            "updated_at": NOW.isoformat(),
        }
        db.execute(
            f"INSERT INTO ingest_accounts({','.join(columns)}) "  # noqa: S608
            f"VALUES ({','.join('?' * len(columns))})",
            [values.get(c) for c in columns],
        )
    observe_orders(erp, channel, "conta", sale("A1", status=status))
    plan = plan_of(erp, channel, "conta")
    assert ("not_open" in plan["blocked"]) is (not open_)


def test_seller_code_links_only_when_it_is_exactly_a_product(ml_account):
    observe_orders(
        ml_account,
        "ml",
        ML_STORE,
        sale("A1", sku="demo.aparelho"),
        sale("A2", sku="codigo-que-nao-existe"),
    )
    capture_buyer(ml_account, "A1")
    capture_buyer(ml_account, "A2")
    plan = plan_of(ml_account)
    assert plan["ready"] == ["A1"] and plan["blocked"] == {"item_missing": 1}


def test_order_cancelled_at_the_store_is_flagged_and_keeps_its_reservation(ml_account):
    from app.erp_local.bling_compat import _current_situation

    observe_orders(ml_account, "ml", ML_STORE, sale("A1"))
    capture_buyer(ml_account, "A1")
    created = native_ingest.promote(ml_account, "ml", ML_STORE, "A1", depot="geral")
    before = balance(ml_account)
    assert native_ingest.sync_promoted(ml_account, "ml", ML_STORE)["flagged"] == []
    observe_orders(
        ml_account,
        "ml",
        ML_STORE,
        {**sale("A1", status="cancelled"), "updated_at": (NOW + timedelta(hours=1)).isoformat()},
    )
    assert native_ingest.sync_promoted(ml_account, "ml", ML_STORE)["flagged"] == [created["id"]]
    assert native_ingest.sync_promoted(ml_account, "ml", ML_STORE)["flagged"] == []
    with ml_account.connection() as db:
        doc, _ = ml_account.document(db, created["id"], "order")
        assert _current_situation(db, doc) == native_ingest.VERIFY_CANCELLATION
    assert balance(ml_account) == before


def test_ml_total_is_products_plus_buyer_freight_minus_coupon(ml_account):
    observe_orders(
        ml_account,
        "ml",
        ML_STORE,
        {**sale("A1"), "coupon": _money("2.50")},
        {**sale("A2"), "_freight": "3.00"},
    )
    capture_buyer(ml_account, "A1")
    capture_buyer(ml_account, "A2")
    for external, expected, field, gap in [
        ("A1", "7.50", "discount", "2.50"),
        ("A2", "13.00", "freight", "3.00"),
    ]:
        created = native_ingest.promote(ml_account, "ml", ML_STORE, external, depot="geral")
        with ml_account.connection() as db:
            _, data = ml_account.document(db, created["id"], "order")
        assert data["total"] == expected and data[field] == gap


def test_order_without_a_store_total_keeps_the_sum_of_its_items(ml_account):
    observe_orders(ml_account, "ml", ML_STORE, sale("A1"))
    capture_buyer(ml_account, "A1")
    created = native_ingest.promote(ml_account, "ml", ML_STORE, "A1", depot="geral")
    with ml_account.connection() as db:
        _, data = ml_account.document(db, created["id"], "order")
    assert data["total"] == "10.00" and "discount" not in data


def test_discount_larger_than_the_order_is_refused(erp):
    with pytest.raises(ERPError, match="Desconto maior"):
        run(
            erp,
            "order.import",
            {
                "channel": "ml",
                "store": "c",
                "external_id": "X",
                "depot": "geral",
                "items": [{"sku": "demo.aparelho", "quantity": "1", "unit_price": "10"}],
                "customer_name": "Ana",
                "discount": "11.00",
            },
        )


def test_tiktok_lines_count_one_unit_and_use_the_sale_price(erp):
    with erp.connection() as db:
        columns = [r[1] for r in db.execute("PRAGMA table_info(ingest_accounts)")]
        values = {
            "channel": "tiktok",
            "store": "conta",
            "enabled": 1,
            "authority": "own",
            "failures": 0,
            "updated_at": NOW.isoformat(),
        }
        db.execute(
            f"INSERT INTO ingest_accounts({','.join(columns)}) "  # noqa: S608
            f"VALUES ({','.join('?' * len(columns))})",
            [values.get(c) for c in columns],
        )
    line = {
        "line_id": "1",
        "listing_id": "L",
        "variation_id": "V",
        "seller_sku": "demo.aparelho",
        "quantity": None,
        "sale_price": _money("15.00"),
    }
    observe_orders(
        erp,
        "tiktok",
        "conta",
        {
            "id": "T1",
            "status": "AWAITING_SHIPMENT",
            "items": [line, {**line, "line_id": "2"}],
            "total": _money("30.00"),
        },
    )
    plan = plan_of(erp, "tiktok", "conta")
    assert "quantity_unknown" not in plan["blocked"] and "price_unknown" not in plan["blocked"]


def test_ml_freight_and_coupon_both_show_like_bling(ml_account):
    observe_orders(
        ml_account,
        "ml",
        ML_STORE,
        {**sale("A1"), "paid": _money("10.00"), "_freight": "27.99", "coupon": _money("4.00")},
    )
    capture_buyer(ml_account, "A1")
    created = native_ingest.promote(ml_account, "ml", ML_STORE, "A1", depot="geral")
    with ml_account.connection() as db:
        _, data = ml_account.document(db, created["id"], "order")
    assert data["total"] == "33.99" and data["freight"] == "27.99" and data["discount"] == "4.00"


def test_ml_sale_without_freight_read_waits(ml_account):
    observe_orders(ml_account, "ml", ML_STORE, {**sale("A1"), "_freight": False})
    capture_buyer(ml_account, "A1")
    assert plan_of(ml_account)["blocked"] == {"total_unknown": 1}
    with pytest.raises(ERPError, match="Frete ou cupom"):
        native_ingest.promote(ml_account, "ml", ML_STORE, "A1", depot="geral")


def test_store_code_wins_over_a_stale_listing_link(erp, monkeypatch):
    from app.erp_local import native_orders

    monkeypatch.setattr(
        native_orders,
        "resolve_item",
        lambda *_a, **_k: {"sku": "demo.kit", "kind": "kit", "state": "matched"},
    )
    with erp.connection() as db:
        own = native_ingest._resolve(db, "s", {"seller_sku": "demo.aparelho"}, "shopee")
        unknown = native_ingest._resolve(db, "s", {"seller_sku": "nao-existe"}, "shopee")
    assert own["sku"] == "demo.aparelho" and own["state"] == "matched"
    assert unknown["sku"] == "demo.kit"


def test_tiktok_total_is_products_plus_buyer_shipping_without_handling_fee(erp):
    with erp.connection() as db:
        paid = {"shipping_fee": _money("10.17"), "total": _money("2789.94")}
        assert native_ingest.store_charges(db, "tiktok", "s", "K", [paid]) == {
            "freight": Decimal("10.17"),
            "coupon": Decimal(0),
        }
        assert native_ingest.store_charges(db, "tiktok", "s", "K", [{"total": _money("1")}]) is None
        assert native_ingest.store_charges(db, "shopee", "s", "K", [paid]) is None


def test_re_read_keeps_what_a_partial_reading_brought_and_goes_on(account):
    go(account, Provider(fake_read()))
    days = native_ingest.FIRST_LOOKBACK_WINDOW.days + 1
    partial = fake_read(state="partial_read", orders=[{"updated_at": NOW.isoformat()}])
    provider = Provider(partial, *[fake_read()] * days)
    result = native_ingest.run_lookback(
        account, CHANNEL, ACCOUNT, provider, now=NOW, importer=importer
    )
    assert result["state"] == "partial_kept" and result["added"] == 1
    assert len(provider.calls) == native_ingest.FIRST_LOOKBACK_WINDOW.days


def test_own_order_numbers_start_after_bling_range():
    from app.erp_local import bling_compat

    assert bling_compat.own_number(bling_compat.FIRST_OWN_ID + 1) == 900_001
    assert bling_compat.own_number(27263613894) == 27263613894
