"""Server rounds with fake transports: nothing leaves this process."""

import json
import subprocess
import sys

import pytest

from app.erp_local import native_ingest, service_loop


class Done:
    def __init__(self, out=b"{}", code=0):
        self.stdout, self.returncode = out, code


def test_on_the_server_the_script_runs_here_without_ssh(monkeypatch):
    calls = []
    monkeypatch.setenv("ERP_READ_TRANSPORT", "local")
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **kw: calls.append((cmd, kw)) or Done(b'{"a":1}')
    )
    assert native_ingest.run_on_host(b"print(1)", {"x": 1}) == {"a": 1}
    assert calls[0][0][:2] == [sys.executable, "-"] and calls[0][1]["input"] == b"print(1)"


def test_elsewhere_it_goes_through_ssh_and_refuses_odd_aliases(monkeypatch):
    calls = []
    monkeypatch.delenv("ERP_READ_TRANSPORT", raising=False)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd) or Done())
    native_ingest.run_on_host(b"", {}, alias="davinci-prod")
    assert calls[0][0] == "/usr/bin/ssh" and "docker exec" in calls[0][-1]
    with pytest.raises(Exception, match="Alias"):
        native_ingest.run_on_host(b"", {}, alias="x;rm")


def test_failed_read_raises(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: Done(b"", 1))
    with pytest.raises(OSError):
        native_ingest.run_on_host(b"", {})


def test_one_round_serves_enabled_accounts_and_isolates_failures(erp, monkeypatch):
    from app.erp_local import native_orders

    monkeypatch.setattr(native_orders, "import_read", lambda _erp, read: {"observations_added": 0})
    with erp.connection() as db:
        for channel, store, enabled in [("ml", "ok", 1), ("shopee", "falha", 1), ("ml", "off", 0)]:
            db.execute(
                "INSERT INTO store_policies VALUES (?,?,'identity_review','{}',?)",
                (channel, store, erp.now()),
            )
            db.execute(
                "INSERT INTO ingest_accounts(channel,store,enabled,authority,failures,updated_at) "
                "VALUES (?,?,?,'bling',0,?)",
                (channel, store, enabled, erp.now()),
            )

    def provider_for(_platform):
        def read(*, channel, store, date_from, date_to):
            if store == "falha":
                raise OSError("fora do ar")
            account = {"integration_id": store, "state": "read_ok", "orders": [], "has_more": False}
            return {"accounts": [account]}

        return read

    report = service_loop.once(erp, provider_for=provider_for)
    assert set(report) == {"ml:ok", "shopee:falha"}
    assert report["ml:ok"]["reception"]["state"] == "complete"
    assert report["shopee:falha"]["reception"]["state"] != "complete"
    json.dumps(report)


def test_archived_stores_are_skipped_and_light_rounds_read_only_fast_channels(erp, monkeypatch):
    from app.erp_local import native_orders

    monkeypatch.setattr(native_orders, "import_read", lambda _erp, read: {"observations_added": 0})
    with erp.connection() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS reference_rows (kind TEXT, id TEXT, data TEXT NOT NULL,"
            " search TEXT NOT NULL, PRIMARY KEY(kind,id))"
        )
        for store, data in [
            ("banida", {"id": "banida", "status": "active", "archived_at": "2026-11-09T21:41:52"}),
            ("ativa", {"id": "ativa", "status": "active", "archived_at": None}),
        ]:
            db.execute(
                "INSERT INTO reference_rows VALUES ('integrations',?,?,'')",
                (store, json.dumps(data)),
            )
        for channel, store in [("ml", "banida"), ("ml", "ativa"), ("amazon", "lenta")]:
            db.execute(
                "INSERT INTO store_policies VALUES (?,?,'identity_review','{}',?)",
                (channel, store, erp.now()),
            )
            db.execute(
                "INSERT INTO ingest_accounts(channel,store,enabled,authority,failures,updated_at) "
                "VALUES (?,?,1,'bling',0,?)",
                (channel, store, erp.now()),
            )
    asked = []

    def provider_for(_platform):
        def read(*, channel, store, date_from, date_to):
            asked.append(store)
            account = {"integration_id": store, "state": "read_ok", "orders": [], "has_more": False}
            return {"accounts": [account]}

        return read

    light = service_loop.once(erp, provider_for=provider_for, channels=service_loop.FAST_CHANNELS)
    assert light["ml:banida"] == {"state": "archived"} and "amazon:lenta" not in light
    assert set(asked) == {"ativa"}
    full = service_loop.once(erp, provider_for=provider_for)
    assert full["amazon:lenta"]["reception"]["state"] == "complete"


def test_local_reads_run_at_lower_priority(monkeypatch):
    calls = []
    monkeypatch.setenv("ERP_READ_TRANSPORT", "local")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(kw) or Done(b"{}"))
    native_ingest.run_on_host(b"", {})
    assert callable(calls[0]["preexec_fn"])


def test_daily_check_writes_counts_and_never_raises(erp, tmp_path, monkeypatch):
    from app.erp_local import parallel_check

    monkeypatch.setattr(
        parallel_check,
        "local_sales",
        lambda _db, *_robot, **_at: {
            "A": {
                "channel": "ml",
                "items": {},
                "total": "1",
                "document": "",
                "name": "",
                "postal_code": "",
            }
        },
    )
    monkeypatch.setattr(native_ingest, "run_on_host", lambda *a, **k: {})
    out = tmp_path / "conferencia.json"
    service_loop.daily_check(erp, out)
    assert json.loads(out.read_text())["ml"]["fora_do_bling"] == 1

    def broken(*a, **k):
        raise OSError("fora do ar")

    monkeypatch.setattr(native_ingest, "run_on_host", broken)
    service_loop.daily_check(erp, tmp_path / "outra.json")
    assert not (tmp_path / "outra.json").exists()


def test_an_account_stopped_for_its_token_reads_again_once_davinci_renews_it(erp):
    with erp.connection() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS reference_rows (kind TEXT, id TEXT, data TEXT NOT NULL,"
            " search TEXT NOT NULL, PRIMARY KEY(kind,id))"
        )
        integrations = {
            "vita": {"status": "active", "updated_at": "2026-11-11T12:00:27+00:00"},
            "velha": {"status": "active", "updated_at": "2026-11-08T12:00:00+00:00"},
            "banida": {
                "status": "active",
                "archived_at": "2026-11-09T21:41:52",
                "updated_at": "2026-11-11T12:00:00+00:00",
            },
            "pessoa": {"status": "active", "updated_at": "2026-11-11T12:00:00+00:00"},
        }
        for store, data in integrations.items():
            db.execute(
                "INSERT INTO reference_rows VALUES ('integrations',?,?,'')",
                (store, json.dumps({"id": store, **data})),
            )
        for store, state, failures in [
            ("vita", "auth_denied", 1),
            ("velha", "auth_denied", 1),
            ("banida", "auth_denied", 1),
            ("pessoa", None, 0),
        ]:
            db.execute(
                "INSERT INTO ingest_accounts(channel,store,enabled,authority,failures,last_state,"
                "updated_at) VALUES ('shopee',?,0,'bling',?,?,'2026-11-09T19:52:14+00:00')",
                (store, failures, state),
            )
    assert service_loop.revive_renewed(erp) == ["shopee:vita"]
    with erp.connection() as db:
        row = db.execute(
            "SELECT enabled,failures,next_attempt_at FROM ingest_accounts WHERE store='vita'"
        ).fetchone()
    assert tuple(row) == (1, 0, None)
    # Already revived: the same DaVinci credentials do not turn it on twice.
    assert service_loop.revive_renewed(erp) == []
