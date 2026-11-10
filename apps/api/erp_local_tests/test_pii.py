"""Buyer data sealed at rest, one key per sale, erased past the platform's retention window."""

import base64
import json

import pytest
from test_recipient_capture import CHANNEL, STORE, open_order

from app.erp_local import channel_recipients as cr
from app.erp_local import pii
from app.erp_local import recipient_capture as rc

KEY = base64.urlsafe_b64encode(bytes(range(32))).decode()


@pytest.fixture
def sealed(monkeypatch):
    monkeypatch.setenv(pii.KEY_ENV, KEY)
    monkeypatch.delenv(pii.KEY_FILE_ENV, raising=False)


@pytest.fixture
def amazon_reader(monkeypatch):
    # Amazon has no buyer reader yet (restricted roles); this stands in for the future one.
    monkeypatch.setitem(cr.READERS, "amazon", cr.READERS[CHANNEL])


def observe(erp, channel, external, status, stamp):
    with erp.connection() as db:
        db.execute("PRAGMA foreign_keys=OFF")
        columns = [r[1] for r in db.execute("PRAGMA table_info(native_order_observations)")]
        values = {
            "id": f"{external}-{stamp}",
            "channel": channel,
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


def everything(erp):
    with erp.connection() as db:
        table = " ".join(r[0] for r in db.execute("SELECT data FROM native_order_recipients"))
        log = " ".join(
            r[0] for r in db.execute("SELECT payload FROM events WHERE kind='recipient.capture'")
        )
    return table + " " + log


def test_with_a_master_key_the_table_and_the_log_keep_only_ciphertext(erp, sealed):
    assert rc.capture(erp, CHANNEL, STORE, "S1", open_order())["ready"] is True
    stored = everything(erp)
    assert "Ana Souza" not in stored and "52998224725" not in stored and "Exemplo" not in stored
    with erp.connection() as db:
        reading = rc.for_order(db, CHANNEL, STORE, "S1")
        keys = db.execute("SELECT count(*) FROM pii_keys").fetchone()[0]
    assert reading["recipient"]["name"] == "Ana Souza" and keys == 1
    # The same reading again changes nothing.
    assert rc.capture(erp, CHANNEL, STORE, "S1", open_order())["kept"] is True


def test_amazon_buyer_data_is_never_stored_without_the_master_key(erp, amazon_reader, monkeypatch):
    monkeypatch.delenv(pii.KEY_ENV, raising=False)
    monkeypatch.delenv(pii.KEY_FILE_ENV, raising=False)
    result = rc.capture(erp, "amazon", STORE, "AZ1", open_order())
    assert result["state"] == "pii_key_missing"
    assert "Ana Souza" not in everything(erp)
    # Other stores keep working as before while no key is set.
    assert rc.capture(erp, CHANNEL, STORE, "S2", open_order())["ready"] is True


def test_amazon_data_is_erased_30_days_after_shipping_and_only_then(erp, sealed, amazon_reader):
    from datetime import UTC, datetime

    rc.capture(erp, "amazon", STORE, "AZ2", open_order())
    observe(erp, "amazon", "AZ2", "Unshipped", "2026-10-01T10:00:00+00:00")
    observe(erp, "amazon", "AZ2", "Shipped", "2026-10-02T10:00:00+00:00")
    early = datetime(2026, 10, 20, tzinfo=UTC)
    assert pii.purge(erp, early) == {"purged": 0}
    with erp.connection() as db:
        assert rc.for_order(db, "amazon", STORE, "AZ2")["recipient"]["name"] == "Ana Souza"
    late = datetime(2026, 11, 2, tzinfo=UTC)
    assert pii.purge(erp, late) == {"purged": 1}
    with erp.connection() as db:
        assert rc.for_order(db, "amazon", STORE, "AZ2") is None
        key = db.execute("SELECT wrapped,purged_at FROM pii_keys").fetchone()
    assert key["wrapped"] is None and key["purged_at"]
    # The ciphertext still in the log can no longer be opened, and nothing is read again.
    assert rc.capture(erp, "amazon", STORE, "AZ2", open_order())["state"] == "pii_purged"
    assert pii.purge(erp, late) == {"purged": 0}


def test_an_old_plain_reading_in_the_log_is_also_erased(erp, amazon_reader, monkeypatch):
    from datetime import UTC, datetime

    monkeypatch.delenv(pii.KEY_ENV, raising=False)
    monkeypatch.setitem(pii.__dict__, "REQUIRED", set())
    rc.capture(erp, "amazon", STORE, "AZ3", open_order())
    assert "Ana Souza" in everything(erp)
    observe(erp, "amazon", "AZ3", "Canceled", "2026-09-01T10:00:00+00:00")
    assert pii.purge(erp, datetime(2026, 11, 1, tzinfo=UTC)) == {"purged": 1}
    assert "Ana Souza" not in everything(erp)


def test_invalid_master_key_is_refused(monkeypatch):
    from app.erp_local.core import ERPError

    monkeypatch.setenv(pii.KEY_ENV, "curta")
    with pytest.raises(ERPError):
        pii.master()
