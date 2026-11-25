"""While Bling owns the stores, the ERP shows what Bling has now: stock and order situations.

Every round the worker reads DaVinci's copy of Bling (kept current by Bling's own notices) and
sets the ERP to it: the physical balance of each simple product in the source depot, Bling's
reservation beside it, and the number and situation Bling gives each store sale. Nothing is
written to Bling. As soon as any store is switched to the ERP, the stock mirror stops, so it
never overwrites stock the ERP owns.
"""

import json
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from app.erp_local.core import ERPError, number, text

SCHEMA = """
CREATE TABLE IF NOT EXISTS bling_mirror_stock (
 sku TEXT PRIMARY KEY, available TEXT NOT NULL, reserved TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bling_mirror_orders (
 store_number TEXT PRIMARY KEY, number TEXT NOT NULL, situation TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bling_mirror_items (store_number TEXT PRIMARY KEY, items TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bling_mirror_ships (
 id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT NOT NULL, store_number TEXT NOT NULL,
 sku TEXT NOT NULL, quantity TEXT NOT NULL, left_quantity TEXT NOT NULL, seen_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS movement_orders (
 movement_id INTEGER NOT NULL, number TEXT NOT NULL, quantity TEXT NOT NULL, source TEXT NOT NULL,
 PRIMARY KEY (movement_id, number));
"""
STOCK_SQL = (
    "SELECT coalesce(json_agg(t),'[]') FROM (SELECT bling_product_id::text AS id,"
    " stock::text AS available, reserved_stock::text AS reserved, updated_at::text AS updated_at"
    " FROM davinci.products WHERE bling_product_id IS NOT NULL) t"
)
ORDERS_SQL = (
    "SELECT coalesce(json_agg(t),'[]') FROM (SELECT DISTINCT ON (numeroloja)"
    " numeroloja::text AS store_number, numero::text AS number, situacao::text AS situation,"
    " (SELECT coalesce(json_agg(json_build_object('sku', i->>'codigo', 'quantity',"
    " i->>'quantidade')), '[]') FROM jsonb_array_elements(CASE WHEN jsonb_typeof(itens)='array'"
    " THEN itens ELSE '[]'::jsonb END) i) AS items"
    " FROM davinci.bling_orders WHERE data >= current_date - 30 AND numeroloja IS NOT NULL"
    " ORDER BY numeroloja, numero DESC) t"
)
DEPOT = "geral"
REASON = "Espelho do Bling"


def read_local(sql):
    """Same read-only path as the reference, on the server, through DaVinci's settings."""
    import asyncio

    async def run():
        from app.db import SessionLocal, engine

        try:
            async with SessionLocal() as session:
                connection = await session.connection()
                await connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                await connection.exec_driver_sql("SET LOCAL statement_timeout = '25s'")
                value = (await connection.exec_driver_sql(sql)).scalar_one()
                await session.rollback()
                return value
        finally:
            await engine.dispose()

    try:
        value = asyncio.run(run())
    except Exception:
        raise ERPError(
            "source_unavailable", "Não foi possível ler o espelho do Bling.", 502
        ) from None
    return json.loads(value) if isinstance(value, str) else value


def _number(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def switched(db):
    return bool(
        db.execute("SELECT 1 FROM ingest_accounts WHERE authority='own' LIMIT 1").fetchone()
    )


def apply_stock(erp, rows):
    """Physical = Bling's available + reserved; the reservation is kept beside it for the screen."""
    report = {"changed": 0, "same": 0, "skipped": 0, "refused": 0}
    with erp.connection() as db:
        db.executescript(SCHEMA)
        if switched(db):
            return {"state": "stopped", "reason": "loja já trocada para o ERP"}
        origin = {}
        for row in db.execute(
            "SELECT o.sku,o.data FROM catalog_origin o JOIN products p ON p.sku=o.sku "
            "WHERE p.kind='simple'"
        ):
            data = json.loads(row["data"])
            if data.get("bling_product_id"):
                origin[str(data["bling_product_id"])] = (row["sku"], data)
    for row in rows:
        sku, data = origin.get(str(row.get("id")), (None, None))
        available, reserved = _number(row.get("available")), _number(row.get("reserved"))
        if not sku or available is None or reserved is None:
            report["skipped"] += 1
            continue
        physical = available + reserved
        with erp.connection() as db:
            if data.get("stock_state") != "known":
                # Bling's balance is the opening this product was waiting for.
                data = {**data, "stock_state": "known", "stock_basis": "bling_mirror"}
                db.execute("UPDATE catalog_origin SET data=? WHERE sku=?", (json.dumps(data), sku))
            current = erp.balance(db, sku, DEPOT)
            db.execute(
                "INSERT OR REPLACE INTO bling_mirror_stock VALUES (?,?,?,?)",
                (sku, str(available), str(reserved), str(row.get("updated_at") or "")),
            )
        if current == (physical, reserved):
            report["same"] += 1
            continue
        stamp = str(row.get("updated_at") or "")[:26]
        try:
            erp.command(
                f"mirror-v2:{sku[:60]}:{physical}:{reserved}:{stamp}",
                "stock.mirror",
                {"sku": sku, "physical": str(physical), "reserved": str(reserved)},
                actor="espelho do Bling",
            )
        except ERPError:
            report["refused"] += 1
            continue
        report["changed"] += 1
    return report


def apply_command(erp, db, key, p):
    """Set physical and reserved to Bling's, as one recorded movement in the source depot."""
    if not isinstance(p, dict) or set(p) != {"sku", "physical", "reserved"}:
        raise ERPError("mirror_fields", "Informe produto, físico e reservado.")
    if switched(db):
        raise ERPError("mirror_stopped", "Há loja trocada para o ERP; o espelho parou.", 409)
    sku = text(p["sku"], "SKU")
    physical, reserved = number(p["physical"], zero=True), number(p["reserved"], zero=True)
    old_physical, old_reserved = erp.balance(db, sku, DEPOT)
    erp.move(db, key, sku, DEPOT, physical - old_physical, reserved - old_reserved, REASON)
    return {"sku": sku, "physical": str(physical), "reserved": str(reserved)}


# Bling books a sale's exit when it leaves these situations for one past them (15 and on): the
# same rule as the compatible API (bling_compat). Cancelled and held ones are no exit.
RESERVING = {"6", "21", "83965"}
NOT_SHIPPED = RESERVING | {"12", "83955", "83962"}


def _parts(db, sku, quantity, depth=0):
    """A sold code as the physical products that leave: a kit as its parts, times the quantity."""
    row = db.execute("SELECT kind,components FROM products WHERE sku=?", (sku,)).fetchone()
    if row and row["kind"] == "kit" and depth < 4:
        out = {}
        for part in json.loads(row["components"] or "[]"):
            for code, amount in _parts(
                db, part["sku"], quantity * Decimal(str(part["quantity"])), depth + 1
            ).items():
                out[code] = out.get(code, Decimal(0)) + amount
        return out
    return {sku: quantity} if row and row["kind"] == "simple" else {}


def _record_ships(erp, db, rows):
    """Orders that left a reserving situation for a shipped one since the last round: each part
    that leaves, to be matched with the exit Bling books for it (attribute)."""
    before = {
        r["store_number"]: r["situation"]
        for r in db.execute("SELECT store_number,situation FROM bling_mirror_orders")
    }
    now = erp.now()
    for row in rows:
        store_number = str(row.get("store_number") or "")
        old, new = before.get(store_number), str(row.get("situation") or "")
        if old not in RESERVING or not new or new in NOT_SHIPPED:
            continue
        parts = {}
        for line in _lines(row.get("items")):
            for code, amount in _parts(db, line["sku"], Decimal(line["quantity"])).items():
                parts[code] = parts.get(code, Decimal(0)) + amount
        for code, amount in parts.items():
            db.execute(
                "INSERT INTO bling_mirror_ships(number,store_number,sku,quantity,left_quantity,"
                "seen_at) VALUES (?,?,?,?,?,?)",
                (str(row.get("number") or ""), store_number, code, str(amount), str(amount), now),
            )


def apply_orders(erp, rows):
    with erp.connection() as db:
        db.executescript(SCHEMA)
        db.execute("BEGIN IMMEDIATE")
        _record_ships(erp, db, rows)
        db.execute("DELETE FROM bling_mirror_orders")
        db.executemany(
            "INSERT OR REPLACE INTO bling_mirror_orders VALUES (?,?,?)",
            [
                (str(r["store_number"]), str(r["number"]), str(r["situation"]))
                for r in rows
                if r.get("store_number")
            ],
        )
        db.execute("DELETE FROM bling_mirror_items")
        db.executemany(
            "INSERT OR REPLACE INTO bling_mirror_items VALUES (?,?)",
            [
                (str(r["store_number"]), json.dumps(lines))
                for r in rows
                if r.get("store_number") and (lines := _lines(r.get("items")))
            ],
        )
        db.commit()
    return {"orders": len(rows)}


def _lines(raw):
    """Bling's items of a sale (code with its lot, quantity), or [] when they do not read well."""
    if not isinstance(raw, list):
        return []
    lines = []
    for item in raw:
        if not isinstance(item, dict):
            return []
        sku, quantity = str(item.get("sku") or "").strip(), _number(item.get("quantity"))
        if not sku or len(sku) > 120 or quantity is None or quantity <= 0:
            return []
        lines.append({"sku": sku, "quantity": str(quantity)})
    return lines


def family(sku):
    """The product without its lot: dg078.pi+a001.pi and dg078.sp+a001.sp are one family."""
    from app.erp_local import stock_families

    parts = [p.strip() for p in (sku or "").lower().split("+") if p.strip()]
    return "+".join(
        p[: -(len(stock_families.lot(p)) + 1)] if stock_families.lot(p) else p for p in parts
    )


def items(db, store_number):
    try:
        row = db.execute(
            "SELECT items FROM bling_mirror_items WHERE store_number=?", (str(store_number),)
        ).fetchone()
    except Exception:  # noqa: BLE001 - table appears with the first mirror pass
        return []
    return json.loads(row[0]) if row else []


def with_bling_lots(db, store_number, lines):
    """While Bling has the sale, its items show the lot Bling has (the one DaVinci's robot chose),
    not the lot the listing points to. Paired by product and quantity: a line Bling split in two
    lots shows both. Only what is shown changes; the store's values stay as read."""
    left = [{"sku": b["sku"], "quantity": Decimal(b["quantity"])} for b in items(db, store_number)]
    if not left:
        return lines
    shown = []
    for line in lines:
        mine = family(line.get("sku") or line.get("seller_sku"))
        need = line.get("quantity")
        taken = []
        for b in left:
            if not mine or family(b["sku"]) != mine or b["quantity"] <= 0:
                continue
            if need is not None and need <= 0:
                break
            use = b["quantity"] if need is None else min(b["quantity"], need)
            taken.append((b["sku"], use))
            b["quantity"] -= use
            if need is not None:
                need -= use
        if not taken or (len(taken) == 1 and taken[0][0] == line.get("sku")):
            shown.append(line)
            continue
        if need:  # Bling has fewer of this product: the rest stays as the store has it
            taken.append((line.get("sku") or "", need))
        price = line.get("unit_price")
        for sku, quantity in taken:
            row = db.execute("SELECT name FROM products WHERE sku=?", (sku,)).fetchone()
            shown.append(
                {
                    **line,
                    "sku": sku,
                    "name": row["name"] if row else line.get("name", ""),
                    "quantity": quantity,
                    "total": quantity * price if price is not None else None,
                    "erp_sku": line.get("sku") or "",
                    "lot_from_bling": sku != (line.get("sku") or ""),
                }
            )
    return shown


def stock_of(db, sku):
    try:
        row = db.execute(
            "SELECT available,reserved,updated_at FROM bling_mirror_stock WHERE sku=?", (sku,)
        ).fetchone()
    except Exception:  # noqa: BLE001 - table appears with the first mirror pass
        return None
    return dict(row) if row else None


def orders(db):
    try:
        return {
            r["store_number"]: {"number": r["number"], "situation": r["situation"]}
            for r in db.execute("SELECT * FROM bling_mirror_orders")
        }
    except Exception:  # noqa: BLE001
        return {}


# An exit and the order that caused it reach the ERP within a round or two of each other (Bling's
# balance and the order's situation come by different notices): they are paired within this window,
# oldest first, as Bling booked them.
PAIR_WINDOW = timedelta(minutes=30)


def attribute(erp, now=None):
    """Each exit the mirror recorded gets the orders that left in the same minutes, one unit each,
    as Bling shows a "V <número>" beside each exit. What finds no order stays without one."""
    moment = now or datetime.fromisoformat(erp.now())
    since = (moment - PAIR_WINDOW).isoformat()
    paired = 0
    with erp.connection() as db:
        db.executescript(SCHEMA)
        db.execute("BEGIN IMMEDIATE")
        ships = db.execute(
            "SELECT * FROM bling_mirror_ships WHERE CAST(left_quantity AS REAL)>0 AND seen_at>=? "
            "ORDER BY seen_at,id",
            (since,),
        ).fetchall()
        by_sku = {}
        for ship in ships:
            by_sku.setdefault(ship["sku"], []).append(
                [ship["id"], ship["number"], Decimal(ship["left_quantity"]), ship["seen_at"]]
            )
        for sku, pending in by_sku.items():
            exits = [
                [
                    row["id"],
                    row["created_at"],
                    -Decimal(row["physical_delta"]) - Decimal(str(row["done"])),
                ]
                for row in db.execute(
                    "SELECT m.id,m.created_at,m.physical_delta,(SELECT coalesce(sum(CAST("
                    "o.quantity AS REAL)),0) FROM movement_orders o WHERE o.movement_id=m.id) AS "
                    "done FROM movements m WHERE m.sku=? AND m.reason=? AND CAST(m.physical_delta "
                    "AS REAL)<0 AND m.created_at>=? ORDER BY m.created_at,m.id",
                    (sku, REASON, since),
                )
            ]
            # Each order goes to the exit nearest in time: both come within a round or two.
            for ship in pending:
                seen = datetime.fromisoformat(ship[3])
                while ship[2] > 0:
                    open_exits = [e for e in exits if e[2] > 0]
                    if not open_exits:
                        break
                    near = min(open_exits, key=lambda e: abs(datetime.fromisoformat(e[1]) - seen))
                    take = min(near[2], ship[2])
                    db.execute(
                        "INSERT INTO movement_orders VALUES (?,?,?,'espelho') ON CONFLICT"
                        "(movement_id,number) DO UPDATE SET quantity=CAST(CAST(quantity AS REAL)+"
                        "CAST(excluded.quantity AS REAL) AS TEXT)",
                        (near[0], ship[1], str(take)),
                    )
                    near[2] -= take
                    ship[2] -= take
                    paired += 1
                    db.execute(
                        "UPDATE bling_mirror_ships SET left_quantity=? WHERE id=?",
                        (str(ship[2]), ship[0]),
                    )
        db.execute(
            "DELETE FROM bling_mirror_ships WHERE seen_at<?",
            ((moment - timedelta(days=2)).isoformat(),),
        )
        db.commit()
    return {"pairs": paired}


# Exits from before the pairing (or that found no order in time): the orders whose store marked
# them shipped (DaVinci's Logística, the store's own time, never an estimate) in the minutes
# before the exit, in the order they shipped, as many units as the exit has.
HISTORY_SQL = (
    "SELECT coalesce(json_agg(t),'[]') FROM (SELECT pedido_bling AS number,"
    " status_datas->'logistics_status'->>'em' AS logistics,"
    " status_datas->'logistics_status'->>'fonte' AS logistics_source,"
    " status_datas->'order_status'->>'em' AS ordered,"
    " status_datas->'order_status'->>'fonte' AS ordered_source"
    " FROM davinci.logistica WHERE data >= current_date - 30 AND pedido_bling IS NOT NULL) t"
)
HISTORY_BEFORE, HISTORY_AFTER = timedelta(minutes=15), timedelta(minutes=2)


def _when(value):
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return moment if moment.tzinfo else None


def attribute_history(erp, reader=read_local, now=None):
    times = {}
    for row in reader(HISTORY_SQL) or []:
        for key in ("logistics", "ordered"):
            moment = _when(row.get(key)) if row.get(key + "_source") == "plataforma" else None
            if moment and str(row.get("number") or "") not in times:
                times[str(row["number"])] = moment
    moment = now or datetime.fromisoformat(erp.now())
    paired = 0
    with erp.connection() as db:
        db.executescript(SCHEMA)
        db.execute("BEGIN IMMEDIATE")
        mirrored = {
            r["number"]: (r["store_number"], r["situation"])
            for r in db.execute("SELECT number,store_number,situation FROM bling_mirror_orders")
        }
        taken = {r[0] for r in db.execute("SELECT DISTINCT number FROM movement_orders")}
        units = {}
        for number, shipped in times.items():
            store_number, situation = mirrored.get(number, (None, None))
            if not store_number or number in taken or situation in NOT_SHIPPED:
                continue
            for line in items(db, store_number):
                for code, amount in _parts(db, line["sku"], Decimal(line["quantity"])).items():
                    units.setdefault(code, []).append([shipped, number, amount])
        for pending in units.values():
            pending.sort(key=lambda u: (u[0], u[1]))
        exits = db.execute(
            "SELECT m.id,m.sku,m.created_at,m.physical_delta,(SELECT coalesce(sum(CAST(o.quantity"
            " AS REAL)),0) FROM movement_orders o WHERE o.movement_id=m.id) AS done FROM movements"
            " m WHERE m.reason=? AND CAST(m.physical_delta AS REAL)<0 AND m.created_at<? "
            "ORDER BY m.created_at,m.id",
            (REASON, (moment - PAIR_WINDOW).isoformat()),
        ).fetchall()
        for row in exits:
            pending = units.get(row["sku"])
            at = _when(row["created_at"])
            if not pending or not at:
                continue
            left = -Decimal(row["physical_delta"]) - Decimal(str(row["done"]))
            for unit in pending:
                if left <= 0:
                    break
                if unit[2] <= 0 or not at - HISTORY_BEFORE <= unit[0] <= at + HISTORY_AFTER:
                    continue
                take = min(left, unit[2])
                db.execute(
                    "INSERT INTO movement_orders VALUES (?,?,?,'historico') ON CONFLICT"
                    "(movement_id,number) DO UPDATE SET quantity=CAST(CAST(quantity AS REAL)+"
                    "CAST(excluded.quantity AS REAL) AS TEXT)",
                    (row["id"], unit[1], str(take)),
                )
                unit[2] -= take
                left -= take
                paired += 1
        db.commit()
    return {"pairs": paired}


_HISTORY_EVERY = timedelta(hours=6)
_history_at = None


def run(erp, reader=read_local):
    """One pass: stock and situations, each on its own so one failing keeps the other; then each
    new exit gets the order that caused it."""
    report = {}
    for name, sql, apply in (
        ("estoque", STOCK_SQL, apply_stock),
        ("situacoes", ORDERS_SQL, apply_orders),
    ):
        try:
            report[name] = apply(erp, reader(sql))
        except ERPError as error:
            report[name] = {"state": error.code}
    try:
        report["pedidos"] = attribute(erp)
    except ERPError as error:
        report["pedidos"] = {"state": error.code}
    global _history_at
    moment = datetime.fromisoformat(erp.now())
    if _history_at is None or moment - _history_at >= _HISTORY_EVERY:
        _history_at = moment
        try:
            report["pedidos_antigos"] = attribute_history(erp, reader)
        except ERPError as error:
            report["pedidos_antigos"] = {"state": error.code}
    return report
