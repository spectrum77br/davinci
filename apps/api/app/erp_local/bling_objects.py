"""Bling's "Objetos de postagem", copied read only while Bling still keeps the shipments.

Bling keeps one object per order volume (`pedido.transporte.volumes[].id`): the service, the
tracking code and Bling's own tracking situation, named as its screen names them
(templates/logisticTrackingStatus.js, read 23/11/2026). Its API has no list of objects, so the
copy reads each order of the last days once, then its objects, and reads again only the objects
still moving. Everything goes the shadow check's read-only way: GET only, on the server, with
DaVinci's Bling login, never a token refresh. The client's name is kept sealed, as every buyer
datum in the ERP (pii).

After Bling, the screen shows the ERP's own objects (store_objects); `compare` says how close the
tracking the ERP reads from the stores already is to Bling's.
"""

import json
from datetime import UTC, datetime, timedelta

from app.erp_local import compat_shadow, pii
from app.erp_local.bling_payables import UnavailableError, _batches, _data, _read
from app.erp_local.core import dumps

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS bling_object_logistics (id TEXT PRIMARY KEY, name TEXT NOT NULL,"
    " kind TEXT, situation TEXT, services TEXT NOT NULL, read_at TEXT NOT NULL);"
    "CREATE TABLE IF NOT EXISTS bling_object_orders (order_id TEXT PRIMARY KEY,"
    " number TEXT NOT NULL, store TEXT, store_number TEXT, channel TEXT, day TEXT,"
    " objects TEXT NOT NULL, client TEXT, read_at TEXT NOT NULL);"
    "CREATE TABLE IF NOT EXISTS bling_objects (id TEXT PRIMARY KEY, order_id TEXT NOT NULL,"
    " service_id TEXT, service TEXT, tracking TEXT, status INTEGER, description TEXT,"
    " status_at TEXT, departure TEXT, read_at TEXT NOT NULL);"
    "CREATE TABLE IF NOT EXISTS bling_objects_runs (taken_at TEXT PRIMARY KEY, data TEXT NOT NULL);"
)
# Bling's names for `rastreamento.situacao` (logisticTrackingStatus.js). 8 has no constant there
# and is what Bling shows for the volumes it never tracks (Shopee, Temu, Amazon DBA): "Não postado".
STATUS = {
    0: "Postada",
    1: "Em andamento",
    2: "Não entregue",
    3: "Entregue",
    4: "Aguardando retirada",
    5: "Etiqueta paga",
    6: "Vinculado",
    7: "Atrasado",
    8: "Não postado",
    9: "Suspenso",
    11: "Agendamento programado",
    12: "Aguardando disponibilidade",
    13: "Disponível para envio",
}
DELIVERED = 3
DAYS = 31  # Bling's screen opens on the last 30 days
KEEP_DAYS = 40
CALLS_PER_ROUND = 60  # orders and objects together, in DaVinci's Bling turn
LOGISTICS_EVERY = timedelta(hours=24)
# A moving object is read again every 6 h; one Bling does not track (8) or still waits, daily.
MOVING = {0, 1, 4, 7, 11, 12, 13}
REREAD_MOVING, REREAD_OTHER = timedelta(hours=6), timedelta(hours=24)
# One JSON value, as bling_mirror.read_local reads it.
ORDERS_SQL = (
    "SELECT coalesce(json_agg(t),'[]') FROM ("
    "SELECT DISTINCT ON (o.bling_id) o.bling_id::text AS id, o.numero::text AS number,"
    " o.loja::text AS store, coalesce(o.numeroloja::text,'') AS store_number,"
    " o.data::text AS day, s.marketplace AS channel"
    " FROM davinci.bling_orders o LEFT JOIN davinci.stores s ON s.bling_store_id::text = o.loja"
    " WHERE o.data >= current_date - 31 AND o.bling_id IS NOT NULL"  # DAYS
    " ORDER BY o.bling_id, o.updated_at DESC) t"
)


def _iso(moment):
    return moment.isoformat()


def _when(value):
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _logistics(db, reader, moment):
    newest = db.execute("SELECT max(read_at) FROM bling_object_logistics").fetchone()[0]
    if newest and moment - (_when(newest) or moment) < LOGISTICS_EVERY:
        return 0
    calls = [("/logisticas", [["pagina", str(p)], ["limite", "100"]]) for p in (1, 2, 3)]
    rows = [row for result in _read(reader, calls) for row in (_data(result) or [])]
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        services = [
            str(s.get("id"))
            for s in row.get("servicos") or []
            if isinstance(s, dict) and s.get("id")
        ]
        db.execute(
            "INSERT OR REPLACE INTO bling_object_logistics VALUES (?,?,?,?,?,?)",
            (
                str(row["id"]),
                str(row.get("descricao") or "").strip() or f"Logística {row['id']}",
                str(row.get("tipoIntegracao") or ""),
                str(row.get("situacao") or ""),
                dumps(services),
                _iso(moment),
            ),
        )
    return len(calls)


def _seal_client(db, order, name):
    if not name:
        return None
    try:
        sealed = pii.seal(db, f"bling-objects:{order['id']}", order.get("channel") or "", name)
    except Exception:  # noqa: BLE001 - no key for a channel that requires one: keep no name
        return None
    return dumps(sealed)


def _read_orders(db, reader, orders, moment):
    calls = [(f"/pedidos/vendas/{o['id']}", []) for o in orders]
    for batch_orders, batch_calls in zip(_batches(orders), _batches(calls), strict=True):
        for order, result in zip(batch_orders, _read(reader, batch_calls), strict=True):
            detail = _data(result) or {}
            volumes = (detail.get("transporte") or {}).get("volumes") or []
            objects = [str(v["id"]) for v in volumes if isinstance(v, dict) and v.get("id")]
            client = _seal_client(db, order, (detail.get("contato") or {}).get("nome"))
            db.execute(
                "INSERT OR REPLACE INTO bling_object_orders VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    order["id"],
                    str(detail.get("numero") or order.get("number") or ""),
                    order.get("store"),
                    str(detail.get("numeroLoja") or order.get("store_number") or ""),
                    order.get("channel"),
                    str(order.get("day") or "")[:10],
                    dumps(objects),
                    client,
                    _iso(moment),
                ),
            )
    return len(calls)


def _read_objects(db, reader, wanted, moment):
    calls = [(f"/logisticas/objetos/{identifier}", []) for identifier, _ in wanted]
    for batch, batch_calls in zip(_batches(wanted), _batches(calls), strict=True):
        for (identifier, order_id), result in zip(batch, _read(reader, batch_calls), strict=True):
            data = _data(result) or {}
            service = data.get("servico") or {}
            tracking = data.get("rastreamento") or {}
            status = tracking.get("situacao")
            db.execute(
                "INSERT OR REPLACE INTO bling_objects VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    order_id,
                    str(service.get("id") or "") or None,
                    str(service.get("nome") or ""),
                    str(tracking.get("codigo") or ""),
                    int(status)
                    if isinstance(status, int | str) and str(status).isdigit()
                    else None,
                    str(tracking.get("descricao") or "")[:300],
                    str(tracking.get("ultimaAlteracao") or ""),
                    str(data.get("dataSaida") or "")[:10],
                    _iso(moment),
                ),
            )
    return len(calls)


def _due_objects(db, moment, limit):
    """Objects never read first, then the ones whose situation may have moved."""
    known = {
        r["id"]: (r["status"], _when(r["read_at"]))
        for r in db.execute("SELECT id,status,read_at FROM bling_objects")
    }
    fresh, stale = [], []
    for order_id, objects in db.execute("SELECT order_id,objects FROM bling_object_orders"):
        for identifier in json.loads(objects):
            if identifier not in known:
                fresh.append((identifier, order_id))
                continue
            status, read_at = known[identifier]
            if status == DELIVERED or read_at is None:
                continue
            wait = REREAD_MOVING if status in MOVING else REREAD_OTHER
            if moment - read_at >= wait:
                stale.append((read_at, identifier, order_id))
    stale.sort()
    return (fresh + [(i, o) for _, i, o in stale])[:limit]


def _forget_old(db, moment):
    """Orders out of the window leave the copy, and their client's key is erased."""
    limit = (moment - timedelta(days=KEEP_DAYS)).date().isoformat()
    old = [
        r[0] for r in db.execute("SELECT order_id FROM bling_object_orders WHERE day<?", (limit,))
    ]
    for order_id in old:
        db.execute("DELETE FROM bling_objects WHERE order_id=?", (order_id,))
        db.execute("DELETE FROM bling_object_orders WHERE order_id=?", (order_id,))
        db.execute(
            "UPDATE pii_keys SET wrapped=NULL,purged_at=? WHERE ref=?",
            (_iso(moment), f"bling-objects:{order_id}"),
        )
    return len(old)


def run(erp, reader=None, *, now=None, orders_reader=None, calls=CALLS_PER_ROUND):
    """One round: Bling's logistics once a day, then new orders and their objects, then the
    objects that may have moved, within a few dozen reads so DaVinci keeps its Bling turn."""
    from app.erp_local import bling_mirror

    reader = reader or compat_shadow.host_reader
    orders_reader = orders_reader or bling_mirror.read_local
    moment = now or datetime.now(UTC)
    with erp.connection() as db:
        db.executescript(SCHEMA + pii.SCHEMA)
    report = {"taken_at": _iso(moment), "state": "read_ok", "orders": 0, "objects": 0}
    try:
        candidates = [dict(o) for o in orders_reader(ORDERS_SQL) or []]
        with erp.connection() as db:
            used = _logistics(db, reader, moment)
            db.commit()
            report["forgotten"] = _forget_old(db, moment)
            db.commit()
            known = {r[0] for r in db.execute("SELECT order_id FROM bling_object_orders")}
            cutoff = (moment - timedelta(days=KEEP_DAYS)).date().isoformat()
            new = sorted(
                (
                    o
                    for o in candidates
                    if str(o.get("id")) not in known and str(o.get("day") or "")[:10] >= cutoff
                ),
                key=lambda o: (str(o.get("day") or ""), str(o.get("id"))),
                reverse=True,
            )
            room = max(0, (calls - used) // 2)
            if new and room:
                for order in new[:room]:
                    order["id"] = str(order["id"])
                report["orders"] = _read_orders(db, reader, new[:room], moment)
                db.commit()
            due = _due_objects(db, moment, max(0, calls - used - report["orders"]))
            if due:
                report["objects"] = _read_objects(db, reader, due, moment)
            report["orders_missing"] = max(0, len(new) - report["orders"])
            db.commit()
    except UnavailableError as error:
        report["state"] = str(error)
    except Exception as error:  # noqa: BLE001 - a copy that cannot read keeps the last one
        report["state"] = type(error).__name__
    with erp.connection() as db:
        db.execute(
            "INSERT OR REPLACE INTO bling_objects_runs VALUES (?,?)",
            (report["taken_at"], dumps(report)),
        )
        db.execute(
            "DELETE FROM bling_objects_runs WHERE taken_at NOT IN "
            "(SELECT taken_at FROM bling_objects_runs ORDER BY taken_at DESC LIMIT 48)"
        )
        db.commit()
    return report


def _service_logistics(db):
    out, names = {}, {}
    for row in db.execute("SELECT id,name,services FROM bling_object_logistics"):
        names[row["id"]] = row["name"]
        for service in json.loads(row["services"]):
            out[service] = row["id"]
    return out, names


def listing(db, logistics="", query="", date_from="", date_to="", offset=0, limit=100):
    """The objects as Bling's screen lists them: by logistics, by the day they left."""
    try:
        by_service, names = _service_logistics(db)
        rows = db.execute(
            "SELECT o.*,r.number,r.store_number,r.channel,r.client,r.day FROM bling_objects o "
            "JOIN bling_object_orders r ON r.order_id=o.order_id"
        ).fetchall()
        last = db.execute(
            "SELECT data FROM bling_objects_runs ORDER BY taken_at DESC LIMIT 1"
        ).fetchone()
    except Exception:  # noqa: BLE001 - before the first copy
        return {"items": [], "total": 0, "offset": 0, "logistics": [], "copy": None}
    counts, items = {}, []
    text = (query or "").strip().casefold()
    for row in rows:
        place = by_service.get(row["service_id"] or "", "")
        day = row["departure"] or row["day"] or ""
        if (date_from and day < date_from) or (date_to and day > date_to):
            continue
        counts[place] = counts.get(place, 0) + 1
        if logistics and place != logistics:
            continue
        client = pii.open_value(db, json.loads(row["client"])) if row["client"] else ""
        item = {
            "object": row["id"],
            "number": row["number"],
            "store_number": row["store_number"] or "",
            "remessa": "",
            "client": client or "",
            "service": row["service"] or "",
            "tracking": row["tracking"] or "",
            "situation": STATUS.get(row["status"], "Sem situação"),
            "description": row["description"] or "",
            "departure": day,
            "logistics": names.get(place, ""),
        }
        if (
            text
            and text
            not in " ".join(
                [item["number"], item["store_number"], item["client"], item["tracking"]]
            ).casefold()
        ):
            continue
        items.append(item)
    items.sort(key=lambda i: (i["departure"], i["number"]), reverse=True)
    return {
        "items": items[offset : offset + limit],
        "total": len(items),
        "offset": offset,
        "logistics": sorted(
            (
                {"id": k, "name": names.get(k, "Sem logística"), "objects": v}
                for k, v in counts.items()
            ),
            key=lambda x: x["name"].casefold(),
        ),
        "copy": json.loads(last[0]) if last else None,
    }


def compare(db):
    """Per channel, Bling's tracking codes against the ones the ERP reads from the stores itself:
    what will be left on screen once Bling is gone."""
    try:
        bling = db.execute(
            "SELECT r.channel,r.store_number,o.tracking FROM bling_objects o "
            "JOIN bling_object_orders r ON r.order_id=o.order_id WHERE o.tracking<>''"
        ).fetchall()
        own = {
            r["number"]: json.loads(r["data"]).get("tracking_number") or ""
            for r in db.execute("SELECT number,data FROM store_tracking")
        }
    except Exception:  # noqa: BLE001
        return {}
    report = {}
    for row in bling:
        entry = report.setdefault(
            row["channel"] or "outro", {"bling": 0, "same": 0, "different": 0, "unread": 0}
        )
        entry["bling"] += 1
        mine = own.get(row["store_number"] or "")
        if not mine:
            entry["unread"] += 1
        elif mine.strip().upper() == row["tracking"].strip().upper():
            entry["same"] += 1
        else:
            entry["different"] += 1
    return report
