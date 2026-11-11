"""Homologação: o ERP como dono das lojas num dia de verdade, sem nada sair do servidor.

A cópia nasce da base real e, a cada minuto, recebe dela o que as lojas mandaram: pedidos,
compradores, rastreios e o cadastro do DaVinci. Os pedidos feitos a partir do início do teste viram
pedidos do ERP sozinhos, como o Bling importa os seus; os que a loja ainda não liberou esperam a
próxima rodada. O que a pessoa faz aqui fica só aqui: a base real é aberta só para leitura, e o
container não recebe chave de loja, do Bling, da SEFAZ nem do DaVinci, então não há como mandar
nada para fora.

Uso: python -m app.erp_local.homolog --live <base real> --database <cópia> [--port 8765]
"""

import argparse
import contextlib
import logging
import os
import sqlite3
import threading
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from app.erp_local import native_ingest, stock_check
from app.erp_local.core import ERPError, LocalERP

log = logging.getLogger("erp.homolog")
SCHEMA = "CREATE TABLE IF NOT EXISTS homolog_info (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
# What the stores and DaVinci send, followed from the real base. "ignore" keeps the first copy of a
# row (reads and observations are keyed by content); "replace" follows changes (a buyer read again,
# a tracking update, a key shredded by the 30-day rule); "mirror" takes the whole table as it is.
FOLLOW = {
    "native_order_reads": ("ignore", "captured_at"),
    "native_order_observations": ("ignore", "captured_at"),
    "native_order_recipients": ("replace", "captured_at"),
    "store_tracking": ("replace", "updated_at"),
    "pii_keys": ("replace", None),
    "store_policies": ("ignore", None),
    "ingest_runs": ("ignore", "started_at"),
    # Bling's situation of each store sale, as the real base follows it: the list shows it beside
    # what the ERP does, so the day can be compared order by order.
    "bling_mirror_orders": ("mirror", None),
    # The issuers and the numbering Bling and UpSeller used, so a prepared note takes the next one.
    "fiscal_company_candidates": ("replace", None),
}
# The reception state shown on the screen; who owns the store stays "own" here.
ACCOUNT_STATE = (
    "enabled",
    "cursor",
    "window_from",
    "window_to",
    "failures",
    "next_attempt_at",
    "last_ok_at",
    "last_state",
    "lookback_at",
    "updated_at",
)
REFERENCE_EVERY = 600


def _now():
    return datetime.now(UTC).isoformat()


def info(db):
    try:
        rows = db.execute("SELECT key,value FROM homolog_info").fetchall()
    except sqlite3.Error:
        return None
    return {r[0]: r[1] for r in rows} or None


def seed(live, target):
    """The copy, once: the real base as it is now, with every store owned by the ERP."""
    target = Path(target)
    if target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(Path(live).resolve().as_uri() + "?mode=ro", uri=True)
    copy = sqlite3.connect(target)
    try:
        source.backup(copy)
    finally:
        source.close()
        copy.close()
    erp = LocalERP(target)
    with erp.connection() as db:
        db.executescript(SCHEMA)
        db.execute("INSERT OR REPLACE INTO homolog_info VALUES ('started_at',?)", (_now(),))
        db.execute("INSERT OR REPLACE INTO homolog_info VALUES ('source',?)", (str(live),))
        db.execute("UPDATE ingest_accounts SET authority='own'")
    return True


def _columns(db, schema, table):
    return [r[1] for r in db.execute(f"PRAGMA {schema}.table_info({table})")]


def follow(erp, live, *, since=None, reference=False):
    """Bring what arrived in the real base since `since` (with a margin for late commits)."""
    added = Counter()
    # A URI connection, so the real base is attached read-only: SQLite itself refuses any write.
    with contextlib.closing(
        sqlite3.connect(
            Path(erp.database).resolve().as_uri(), uri=True, timeout=15, isolation_level=None
        )
    ) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("ATTACH DATABASE ? AS live", (Path(live).resolve().as_uri() + "?mode=ro",))
        try:
            db.execute("BEGIN IMMEDIATE")
            for table, (mode, moment) in FOLLOW.items():
                mine, theirs = _columns(db, "main", table), set(_columns(db, "live", table))
                if theirs and not mine:
                    # A table the real base gained after the copy (the Bling mirror's, say).
                    made = db.execute(
                        "SELECT sql FROM live.sqlite_master WHERE type='table' AND name=?", (table,)
                    ).fetchone()
                    db.execute(made[0])
                    mine = _columns(db, "main", table)
                if not mine or not theirs:
                    continue
                cols = ",".join(c for c in mine if c in theirs)
                where, args = "", ()
                if moment and since:
                    where = f" WHERE {moment} > strftime('%Y-%m-%dT%H:%M:%S', ?, '-20 minutes')"
                    args = (since,)
                verb = "INSERT OR IGNORE" if mode == "ignore" else "INSERT OR REPLACE"
                if mode == "mirror":
                    db.execute(f"DELETE FROM main.{table}")  # noqa: S608
                    verb = "INSERT"
                before = db.total_changes
                # Table and column names come from FOLLOW and the schema, never from a request.
                sql = f"{verb} INTO main.{table}({cols}) SELECT {cols} FROM live.{table}{where}"  # noqa: S608
                db.execute(sql, args)
                added[table] = db.total_changes - before
            sets = ",".join(f"{c}=l.{c}" for c in ACCOUNT_STATE)
            db.execute(
                f"UPDATE main.ingest_accounts AS m SET {sets} FROM live.ingest_accounts AS l "  # noqa: S608
                "WHERE m.channel=l.channel AND m.store=l.store"
            )
            db.execute(
                "INSERT OR IGNORE INTO main.ingest_accounts SELECT * FROM live.ingest_accounts"
            )
            db.execute("UPDATE main.ingest_accounts SET authority='own'")
            if reference:
                db.execute("DELETE FROM main.reference_rows")
                before = db.total_changes
                db.execute("INSERT INTO main.reference_rows SELECT * FROM live.reference_rows")
                added["reference_rows"] = db.total_changes - before
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.execute("DETACH DATABASE live")
    return dict(added)


def _imported(db):
    return {
        (r[0], r[1], str(r[2]))
        for r in db.execute(
            "SELECT json_extract(data,'$.channel'),json_extract(data,'$.store'),"
            "json_extract(data,'$.external_id') FROM documents WHERE kind='order'"
        )
    }


def _depot(db, channel, store):
    row = db.execute(
        "SELECT depot,count(*) n FROM stock_routes WHERE channel=? AND store=? "
        "AND depot IS NOT NULL GROUP BY depot ORDER BY n DESC LIMIT 1",
        (channel, store),
    ).fetchone()
    return row[0] if row else "geral"


def auto_import(erp, started_at):
    """Store sales made since the test began become ERP orders, as Bling imports them. A sale the
    store has not released, or with an item or buyer still unknown, waits for the next round."""
    imported, waiting = 0, Counter()
    with erp.connection() as db:
        accounts = db.execute(
            "SELECT channel,store FROM ingest_accounts WHERE authority='own' AND enabled=1"
        ).fetchall()
        done = _imported(db)
    for channel, store in accounts:
        with erp.connection() as db:
            units = native_ingest._units(db, channel, store)
            depot = _depot(db, channel, store)
        for key, unit in units.items():
            if (channel, store, str(key)) in done:
                continue
            created = min((o.get("created_at") or "") for o in unit.values())
            if not created or stock_check._moment(created) < stock_check._moment(started_at):
                continue
            try:
                native_ingest.promote(erp, channel, store, key, depot=depot)
                imported += 1
            except ERPError as error:
                waiting[error.code] += 1
            except (ValueError, KeyError, TypeError):
                waiting["leitura_estranha"] += 1
        try:
            native_ingest.sync_promoted(erp, channel, store)
        except (ERPError, ValueError) as error:
            log.warning("cancelamentos de %s:%s não conferidos: %s", channel, store, error)
    return {"imported": imported, "waiting": dict(waiting)}


def loop(database, live, every=60):
    erp = LocalERP(database)
    last, last_reference, purged_on = None, 0.0, None
    while True:
        started = _now()
        try:
            reference = time.monotonic() - last_reference >= REFERENCE_EVERY
            got = follow(erp, live, since=last, reference=reference)
            if reference:
                last_reference = time.monotonic()
            last = started
            with erp.connection() as db:
                began = (info(db) or {}).get("started_at", started)
            result = auto_import(erp, began)
            from app.erp_local import multi_company

            result["numeração"] = multi_company.follow_numbering(erp)
            log.info("seguiu %s importou %s", got, result)
            today = started[:10]
            if purged_on != today:
                from app.erp_local import pii

                purged_on = today
                log.info("proteção de dados %s", pii.purge(erp))
        except (ERPError, sqlite3.Error, OSError, ValueError) as error:
            log.warning("rodada da homologação falhou: %s", type(error).__name__)
        time.sleep(max(30, every))


def main():
    parser = argparse.ArgumentParser(description="Homologação do ERP com os pedidos reais")
    parser.add_argument("--live", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--every", type=int, default=60)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    # The copy holds buyers: only root reads it, like the real base.
    os.umask(0o077)
    args.database.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(args.database.parent, 0o700)
    for path in args.database.parent.glob(args.database.name + "*"):
        os.chmod(path, 0o600)
    if seed(args.live, args.database):
        log.info("cópia criada a partir da base real")
    threading.Thread(target=loop, args=(args.database, args.live, args.every), daemon=True).start()
    import uvicorn

    from app.erp_local.api import create_app

    uvicorn.run(create_app(args.database), host=args.host, port=args.port, proxy_headers=False)


if __name__ == "__main__":
    main()
