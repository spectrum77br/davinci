"""Run on the API host: Temu sales of one Bling store, as DaVinci's copy of Bling has them.

The ERP has no Temu app of its own yet (it needs each store's authorization), so while Bling is on
its Temu sales come from davinci.bling_orders, read only. The store key is Bling's store code. The
buyer never leaves here: it is read apart, by order number (recipient_capture.bling_copy_reader).
At the switch this reading stops with Bling and the store's own app takes over.
"""

import asyncio
import base64
import json
import re
import sys
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

SOURCE = "temu_bling_copy_v1_read"
LIMIT = 500
# Bling store code -> store, as Bling's store forms name them (observed 10/11/2026).
STORES = {
    "205925390": "Barbosa",
    "206288651": "JLAS",
    "206288703": "Atv",
    "206288707": "Eron",
}
NUMBER = re.compile(r"PO-\d{3}-\d{5,30}")
CANCELLED = "12"
SQL = (
    "SELECT numero::text AS numero, numeroloja::text AS numeroloja,"
    " coalesce(situacao,'')::text AS situacao, data, total, totalprodutos, updated_at,"
    " item_index, item_codigo::text AS item_codigo, item_quantidade, itemvalor, item_desconto"
    " FROM davinci.bling_orders WHERE loja = :store AND numeroloja IN ("
    " SELECT numeroloja FROM davinci.bling_orders WHERE loja = :store"
    " AND left(numeroloja, 3) = 'PO-' GROUP BY numeroloja"
    " HAVING max(updated_at) > :since AND max(updated_at) <= :until)"
    " ORDER BY numeroloja, numero, item_index"
)


def money(value):
    amount = Decimal(str(value or 0)).quantize(Decimal("0.01"), ROUND_HALF_UP)
    return {"amount": str(amount), "currency": "BRL"}


def _stamp(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def project(rows, store, date_from, date_to, now):
    """One account of a read: each Temu sale once, under the first Bling number it got.

    A later Bling order with the same Temu number (an exchange or a resend) is Bling's own, not
    the sale, as in the stock check."""
    by_sale = {}
    for row in rows:
        sale = str(row.get("numeroloja") or "")
        if NUMBER.fullmatch(sale):
            by_sale.setdefault(sale, []).append(row)
    orders = []
    for sale, sale_rows in by_sale.items():
        first = min({str(r["numero"]) for r in sale_rows}, key=lambda n: (len(n), n))
        own = [r for r in sale_rows if str(r["numero"]) == first]
        head = own[0]
        # The window is chosen by the newest change of any Bling order of the sale.
        updated = max(_stamp(r["updated_at"]) for r in sale_rows)
        created = min(_stamp(head["data"]), updated) if head.get("data") else updated
        items = [
            {
                "line_id": str(r["item_index"]),
                "seller_sku": (str(r["item_codigo"]).strip()[:120] or None)
                if r.get("item_codigo")
                else None,
                "quantity": int(r["item_quantidade"] or 0),
                "unit_price": money(r["itemvalor"]),
                "discount": money(r["item_desconto"]),
            }
            for r in own
            if r.get("item_index") is not None
        ]
        situation = str(head.get("situacao") or "")
        orders.append(
            {
                "id": sale,
                "bling_number": first,
                "created_at": created.isoformat(),
                "updated_at": updated.isoformat(),
                "status": "CANCELLED" if situation == CANCELLED else "BLING_COPY",
                "bling_situation": situation,
                "total": money(head["total"]),
                "products_total": money(head["totalprodutos"]),
                "items": items[:200],
            }
        )
    orders.sort(key=lambda o: (o["updated_at"], o["id"]))
    return {
        "integration_id": store,
        "platform": "temu",
        "checked_at": now.isoformat(),
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "state": "read_ok",
        "orders": orders[:LIMIT],
        "has_more": len(orders) > LIMIT,
    }


def envelope(accounts, now):
    return {
        "version": 1,
        "source": SOURCE,
        "read_only": "on",
        "no_token_refresh": True,
        "external_business_writes": False,
        "captured_at": now.isoformat(),
        "limit_per_account": LIMIT,
        "accounts": accounts,
    }


def refused(store, date_from, date_to, now, state):
    return {
        "integration_id": store,
        "platform": "temu",
        "checked_at": now.isoformat(),
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "state": state,
        "orders": [],
        "has_more": None,
    }


async def read(request):
    from sqlalchemy import text

    from app.db import SessionLocal, engine

    window = request["window"]
    date_from = datetime.fromtimestamp(int(window["from"]), UTC)
    date_to = datetime.fromtimestamp(int(window["to"]), UTC)
    accounts = []
    try:
        for store in request["integration_ids"][:4]:
            store = str(store)
            now = datetime.now(UTC)
            if store not in STORES:
                accounts.append(refused(store, date_from, date_to, now, "configuration_missing"))
                continue
            try:
                async with SessionLocal() as session:
                    await session.execute(text("SET TRANSACTION READ ONLY"))
                    rows = (
                        (
                            await session.execute(
                                text(SQL), {"store": store, "since": date_from, "until": date_to}
                            )
                        )
                        .mappings()
                        .all()
                    )
                    await session.rollback()
            except Exception:
                accounts.append(refused(store, date_from, date_to, now, "provider_unavailable"))
                continue
            accounts.append(project([dict(r) for r in rows], store, date_from, date_to, now))
    finally:
        await engine.dispose()
    return envelope(accounts, datetime.now(UTC))


if __name__ == "__main__":
    request = json.loads(base64.b64decode(sys.argv[1], validate=True))
    print(json.dumps(asyncio.run(read(request)), ensure_ascii=False, separators=(",", ":")))
