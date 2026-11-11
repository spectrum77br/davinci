"""Run on the API host: every Bling store sale of the last days, without the buyer.

Only the order and store numbers, the Bling store, the day, the situation and the total leave here.
"""

import asyncio
import base64
import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

SQL = (
    "SELECT DISTINCT ON (o.numero) o.numero, coalesce(o.numeroloja,'') AS numeroloja,"
    " coalesce(o.loja,'') AS loja, i.id::text AS integration_id,"
    " coalesce(i.platform::text, s.marketplace::text) AS platform,"
    " coalesce(i.name, s.apelido_override) AS name,"
    " (o.data AT TIME ZONE 'America/Sao_Paulo')::date::text AS day, o.total, o.situacao"
    " FROM davinci.bling_orders o"
    " LEFT JOIN davinci.stores s ON s.bling_store_id::text = o.loja"
    " LEFT JOIN davinci.integrations i ON i.id = s.integration_id"
    " WHERE o.data >= :since ORDER BY o.numero, o.item_index"
)


def project(rows):
    return [
        {
            "number": str(r["numero"]),
            "store_number": str(r["numeroloja"] or "").strip(),
            "bling_store": str(r["loja"] or ""),
            "store": r["integration_id"],
            "channel": r["platform"],
            "name": (r["name"] or "").strip() or None,
            "day": r["day"],
            "total": str(Decimal(str(r["total"] or 0)).quantize(Decimal("0.01"), ROUND_HALF_UP)),
            "situation": str(r["situacao"] or ""),
        }
        for r in rows
    ]


async def read(days):
    from sqlalchemy import text

    from app.db import SessionLocal, engine

    since = datetime.now(UTC) - timedelta(days=days)
    try:
        async with SessionLocal() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            rows = (await session.execute(text(SQL), {"since": since})).mappings().all()
            await session.rollback()
    finally:
        await engine.dispose()
    return project(rows)


if __name__ == "__main__":
    request = json.loads(base64.b64decode(sys.argv[1], validate=True))
    days = min(max(int(request.get("days") or 7), 1), 31)
    print(json.dumps(asyncio.run(read(days))))
