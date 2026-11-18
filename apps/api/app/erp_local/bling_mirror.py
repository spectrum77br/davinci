"""While Bling owns the stores, the ERP shows what Bling has now: stock and order situations.

Every round the worker reads DaVinci's copy of Bling (kept current by Bling's own notices) and
sets the ERP to it: the physical balance of each simple product in the source depot, Bling's
reservation beside it, and the number and situation Bling gives each store sale. Nothing is
written to Bling. As soon as any store is switched to the ERP, the stock mirror stops, so it
never overwrites stock the ERP owns.
"""

import json
from decimal import Decimal, InvalidOperation

from app.erp_local.core import ERPError, number, text

SCHEMA = """
CREATE TABLE IF NOT EXISTS bling_mirror_stock (
 sku TEXT PRIMARY KEY, available TEXT NOT NULL, reserved TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bling_mirror_orders (
 store_number TEXT PRIMARY KEY, number TEXT NOT NULL, situation TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bling_mirror_items (store_number TEXT PRIMARY KEY, items TEXT NOT NULL);
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


def apply_orders(erp, rows):
    with erp.connection() as db:
        db.executescript(SCHEMA)
        db.execute("BEGIN IMMEDIATE")
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


def run(erp, reader=read_local):
    """One pass: stock and situations, each on its own so one failing keeps the other."""
    report = {}
    for name, sql, apply in (
        ("estoque", STOCK_SQL, apply_stock),
        ("situacoes", ORDERS_SQL, apply_orders),
    ):
        try:
            report[name] = apply(erp, reader(sql))
        except ERPError as error:
            report[name] = {"state": error.code}
    return report
