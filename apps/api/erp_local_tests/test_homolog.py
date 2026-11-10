"""Homologação: a cópia segue a base real só lendo, e o que se faz nela fica nela."""

import sqlite3
from datetime import UTC, datetime, timedelta

from app.erp_local import homolog, native_ingest
from app.erp_local.core import ERPError, LocalERP


def _live(tmp_path):
    live = LocalERP(tmp_path / "live.sqlite3")
    live.command("seed", "demo.seed", {})
    with live.connection() as db:
        db.execute(
            "INSERT INTO ingest_accounts(channel,store,enabled,authority,failures,updated_at,"
            "last_state) VALUES ('ml','loja',1,'bling',0,?,'complete')",
            (live.now(),),
        )
    return live


def _observe(live, digest, external, captured):
    with live.connection() as db:
        db.execute("INSERT INTO native_order_reads VALUES (?,'teste',?,'{}')", (digest, captured))
        db.execute(
            "INSERT INTO native_order_observations VALUES (?,?,'ml','loja',?,?,?,'{}')",
            ("obs-" + digest, digest, external, captured, captured),
        )


def test_seed_copies_once_and_the_erp_owns_every_store(tmp_path):
    live = _live(tmp_path)
    target = tmp_path / "homolog" / "erp.sqlite3"
    assert homolog.seed(live.database, target) is True
    assert homolog.seed(live.database, target) is False
    copy = LocalERP(target)
    with copy.connection() as db:
        assert db.execute("SELECT authority FROM ingest_accounts").fetchone()[0] == "own"
        assert homolog.info(db)["started_at"]
    with live.connection() as db:
        assert db.execute("SELECT authority FROM ingest_accounts").fetchone()[0] == "bling"
        assert homolog.info(db) is None


def test_follow_brings_new_reads_and_keeps_what_was_done_in_the_copy(tmp_path):
    live = _live(tmp_path)
    target = tmp_path / "copia.sqlite3"
    homolog.seed(live.database, target)
    copy = LocalERP(target)
    copy.command("so-aqui", "contact.save", {"data": {"name": "Teste da homologação"}})
    now = datetime.now(UTC)
    _observe(live, "d1", "PED-1", now.isoformat())
    _observe(live, "d0", "PED-0", (now - timedelta(days=2)).isoformat())
    with live.connection() as db:
        db.execute("UPDATE ingest_accounts SET last_state='partial_read',failures=3")
    got = homolog.follow(copy, live.database, since=(now - timedelta(minutes=1)).isoformat())
    assert got["native_order_observations"] == 1
    with copy.connection() as db:
        ids = {r[0] for r in db.execute("SELECT external_id FROM native_order_observations")}
        account = db.execute("SELECT authority,last_state,failures FROM ingest_accounts").fetchone()
        contacts = db.execute("SELECT count(*) FROM own_contacts").fetchone()[0]
    assert "PED-1" in ids and "PED-0" not in ids
    assert tuple(account) == ("own", "partial_read", 3)
    assert contacts == 1
    homolog.follow(copy, live.database)
    with copy.connection() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM native_order_observations WHERE external_id='PED-0'"
            ).fetchone()[0]
            == 1
        )
    # The real base was only read.
    with sqlite3.connect(live.database) as db:
        assert db.execute("SELECT authority FROM ingest_accounts").fetchone()[0] == "bling"
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='homolog_info'").fetchone()


def test_auto_import_takes_only_sales_made_since_the_test_began(tmp_path, monkeypatch):
    live = _live(tmp_path)
    target = tmp_path / "copia.sqlite3"
    homolog.seed(live.database, target)
    copy = LocalERP(target)
    began = datetime.now(UTC)
    units = {
        "antes": {"antes": {"created_at": (began - timedelta(hours=1)).isoformat()}},
        "nova": {"nova": {"created_at": (began + timedelta(minutes=1)).isoformat()}},
        "sem-envio": {"sem-envio": {"created_at": (began + timedelta(minutes=2)).isoformat()}},
    }
    promoted = []

    def promote(_erp, channel, store, key, *, depot):
        if key == "sem-envio":
            raise ERPError("ingest_not_open", "A loja ainda não liberou este pedido.", 409)
        promoted.append((channel, store, key, depot))

    monkeypatch.setattr(native_ingest, "_units", lambda db, channel, store: units)
    monkeypatch.setattr(native_ingest, "promote", promote)
    monkeypatch.setattr(native_ingest, "sync_promoted", lambda *a: {"flagged": []})
    result = homolog.auto_import(copy, began.isoformat())
    assert promoted == [("ml", "loja", "nova", "geral")]
    assert result == {"imported": 1, "waiting": {"ingest_not_open": 1}}
