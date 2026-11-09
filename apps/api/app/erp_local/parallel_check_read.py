"""Run on the API host: Bling's copy of the named sales, with buyer identity as hashes only."""

import asyncio
import base64
import hashlib
import json
import sys
from decimal import ROUND_HALF_UP, Decimal


def fingerprint(value, *, digits=False):
    text = str(value or "").strip()
    text = "".join(c for c in text if c.isdigit()) if digits else " ".join(text.casefold().split())
    return hashlib.sha256(text.encode()).hexdigest()[:16] if text else ""


def summarize(rows):
    """The order's total comes from its first item row, as Bling keeps it there."""
    result = {}
    for row in sorted(rows, key=lambda r: (str(r["numeroloja"]), r.get("item_index") or 0)):
        sale = result.setdefault(
            str(row["numeroloja"]),
            {
                "items": {},
                "total": str(
                    Decimal(str(row["total"] or 0)).quantize(Decimal("0.01"), ROUND_HALF_UP)
                ),
                "document": fingerprint(row["documento_destinatario"], digits=True),
                "name": fingerprint(row["nome_destinatario"]),
                "postal_code": fingerprint(row["cep_destino"], digits=True),
            },
        )
        sku = str(row["item_codigo"] or "?")
        sale["items"][sku] = sale["items"].get(sku, 0) + float(row["item_quantidade"] or 0)
    return result


async def read(keys):
    from sqlalchemy import text

    from app.db import SessionLocal, engine

    try:
        async with SessionLocal() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT numeroloja,item_index,item_codigo,item_quantidade,total,"
                            "documento_destinatario,nome_destinatario,cep_destino "
                            "FROM davinci.bling_orders WHERE numeroloja = ANY(:keys)"
                        ),
                        {"keys": keys},
                    )
                )
                .mappings()
                .all()
            )
            await session.rollback()
    finally:
        await engine.dispose()
    return summarize(rows)


if __name__ == "__main__":
    request = json.loads(base64.b64decode(sys.argv[1], validate=True))
    print(json.dumps(asyncio.run(read([str(k) for k in request["keys"]][:500]))))
