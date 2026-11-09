"""Buyer and recipient kept per order while the store still shows them.

Stores open this data only for a while: Shopee masks it again after PROCESSED. So a complete
reading is kept and a later masked one never replaces it. Only the projection is stored, never the
store's raw reply. No store is read here: the reader is handed in and lives with the credentials.
"""

import hashlib
import json
import os
import re
from datetime import UTC, datetime

from app.erp_local import channel_recipients
from app.erp_local.core import ERPError, dumps, text

SCHEMA = """
CREATE TABLE IF NOT EXISTS native_order_recipients (
 channel TEXT NOT NULL, store TEXT NOT NULL, external_id TEXT NOT NULL, ready INTEGER NOT NULL,
 missing TEXT NOT NULL, data TEXT NOT NULL, read_status TEXT, captured_at TEXT NOT NULL,
 PRIMARY KEY(channel,store,external_id));
"""
COMMANDS = {"recipient.capture"}
CAPTURE_ENV = "ERP_RECIPIENT_CAPTURE"
BATCH = 50
CLOSED = {"cancelled", "canceled", "delivered", "completed", "complete", "returned", "refunded"}
ADDRESS = ["street", "number", "complement", "district", "city", "state", "postal_code"]


def apply(erp, db, p):
    fields = {"channel", "store", "external_id", "read_status", "captured_at", "projection"}
    if not isinstance(p, dict) or set(p) != fields or not isinstance(p["projection"], dict):
        raise ERPError("recipient_fields", "Leitura do destinatário não reconhecida.")
    channel, store = text(p["channel"], "canal"), text(p["store"], "conta")
    external, projection = text(p["external_id"], "pedido"), p["projection"]
    current = db.execute(
        "SELECT ready FROM native_order_recipients WHERE channel=? AND store=? AND external_id=?",
        (channel, store, external),
    ).fetchone()
    ready = bool(projection.get("ready"))
    if current and current["ready"] and not ready:
        return {"kept": True, "ready": True}
    db.execute(
        "INSERT OR REPLACE INTO native_order_recipients VALUES (?,?,?,?,?,?,?,?)",
        (
            channel,
            store,
            external,
            int(ready),
            dumps(projection.get("missing") or []),
            dumps({"buyer": projection["buyer"], "recipient": projection["recipient"]}),
            p["read_status"],
            p["captured_at"],
        ),
    )
    return {"kept": False, "ready": ready}


def capture(erp, channel, store, external, raw, *, status=None, now=None):
    projection = channel_recipients.project(channel, raw)
    with erp.connection() as db:
        stored = for_order(db, channel, store, external)
    if stored and (stored["ready"] or not projection["ready"]):
        same = {"buyer": stored["buyer"], "recipient": stored["recipient"]} == {
            "buyer": projection["buyer"],
            "recipient": projection["recipient"],
        }
        # The same reading every five minutes would only pile up identical records.
        if same or stored["ready"] and not projection["ready"]:
            return {"kept": True, "ready": stored["ready"]}
    moment = (now or datetime.now(UTC)).isoformat()
    digest = hashlib.sha256(dumps([projection, status, moment]).encode()).hexdigest()
    return erp.command(
        f"recipient-v1:{channel}:{store}:{external}:{digest[:32]}",
        "recipient.capture",
        {
            "channel": channel,
            "store": store,
            "external_id": external,
            "read_status": status,
            "captured_at": moment,
            "projection": projection,
        },
        actor="recepção de pedidos",
    )


def for_order(db, channel, store, external):
    row = db.execute(
        "SELECT * FROM native_order_recipients WHERE channel=? AND store=? AND external_id=?",
        (channel, store, external),
    ).fetchone()
    if not row:
        return None
    return {
        **json.loads(row["data"]),
        "ready": bool(row["ready"]),
        "missing": json.loads(row["missing"]),
        "captured_at": row["captured_at"],
    }


def due(db, channel, store):
    """Orders worth reading now: open at the store and still without a complete reading."""
    latest = {}
    for row in db.execute(
        "SELECT external_id,data FROM native_order_observations "
        "WHERE channel=? AND store=? ORDER BY updated_at",
        (channel, store),
    ):
        latest[row["external_id"]] = json.loads(row["data"]).get("status")
    done = {
        row[0]
        for row in db.execute(
            "SELECT external_id FROM native_order_recipients "
            "WHERE channel=? AND store=? AND ready=1",
            (channel, store),
        )
    }
    from app.erp_local.native_ingest import OPEN_STATUS

    # Stores only show the buyer while the sale is released for shipping; unpaid shows nothing.
    allowed = OPEN_STATUS.get(channel)
    result = {}
    for external, status in sorted(latest.items()):
        if external in done or str(status or "").casefold() in CLOSED:
            continue
        if allowed is None or str(status or "").upper() in allowed:
            result[external] = status
    return result


def read_due(erp, channel, store, reader, *, now=None):
    """One capture round. Read failures leave the orders due for the next one."""
    with erp.connection() as db:
        pending = due(db, channel, store)
    if channel not in channel_recipients.READERS:
        return {"state": "not_supported", "due": len(pending), "ready": 0}
    ids = list(pending)[:BATCH]
    if not ids:
        return {"state": "nothing_due", "due": 0, "ready": 0}
    replies = reader(channel=channel, store=store, ids=ids)
    ready = 0
    for external in ids:
        if external in replies:
            done = capture(
                erp, channel, store, external, replies[external], status=pending[external], now=now
            )
            ready += bool(done.get("ready"))
    return {"state": "read", "due": len(pending), "ready": ready}


def summary(db):
    """Counts per account. Names, documents and addresses never reach a report."""
    report = {}
    for row in db.execute("SELECT channel,store,ready,missing FROM native_order_recipients"):
        entry = report.setdefault(
            f"{row['channel']}:{row['store']}", {"ready": 0, "incomplete": 0, "missing": {}}
        )
        entry["ready" if row["ready"] else "incomplete"] += 1
        for reason in json.loads(row["missing"]):
            entry["missing"][reason] = entry["missing"].get(reason, 0) + 1
    return report


def _address(delivery):
    return {
        **{field: delivery[field] for field in ADDRESS if delivery.get(field)},
        **({"ibge_code": delivery["ibge_code"]} if delivery.get("ibge_code") else {}),
    }


def contact_for(erp, captured):
    """The buyer as a customer, on the address of this order. Repeat buyers keep their record."""
    buyer, delivery = captured["buyer"], captured["recipient"]
    document = buyer["document"]
    wanted = {
        "name": buyer["name"][:120],
        "person_type": "J" if len(document) > 11 else "F",
        "document": document,
        **_address(delivery),
    }
    with erp.connection() as db:
        existing = next(
            (
                row
                for row in db.execute("SELECT id,version,data FROM own_contacts")
                if re.sub(r"\D", "", json.loads(row["data"]).get("document") or "") == document
            ),
            None,
        )
        old = json.loads(existing["data"]) if existing else None
    if old and all(old.get(field) == value for field, value in wanted.items()):
        return existing["id"]
    roles = sorted({*(old.get("roles") or []), "customer"}) if old else ["customer"]
    payload = {"data": {**(old or {}), **wanted, "roles": roles}}
    if existing:
        payload.update(id=existing["id"], version=existing["version"])
    digest = hashlib.sha256(dumps(payload).encode()).hexdigest()
    return erp.command("recipient-contact-v1:" + digest, "contact.save", payload)["id"]


def order_recipient(captured):
    """What the label needs, from the delivery side of the reading."""
    delivery = captured["recipient"]
    return {"name": delivery.get("name") or "", **{f: delivery.get(f) or "" for f in ADDRESS}}


def ssh_reader(alias="davinci-prod"):
    """Brings buyer data to wherever the ERP runs, so it opens only where that is the server."""
    from pathlib import Path

    if os.environ.get(CAPTURE_ENV) != "1":
        raise ERPError(
            "recipient_capture_closed", "A leitura de compradores segue fechada aqui.", 409
        )
    here = Path(__file__).parent

    def read(*, channel, store, ids):
        from app.erp_local.native_ingest import run_on_host

        helpers = (here / f"{channel}_order_probe.py").read_text("utf-8")
        script = (
            helpers.split('if __name__ == "__main__":')[0]
            + "\n"
            + (here / "recipient_order_read.py").read_text("utf-8")
        )
        request = {"platform": channel, "integration_id": store, "ids": ids}
        return run_on_host(script.encode("utf-8"), request, alias=alias)

    return read
