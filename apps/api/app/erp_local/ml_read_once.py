"""Bounded GET-only probe, sent through stdin to the existing API container.

No client with automatic OAuth refresh is used. Token remains server-side.
Only a projection without buyer, address, payment or shipping contact is emitted.
"""

import asyncio
import base64
import json
import sys
from datetime import UTC, datetime
from uuid import UUID


async def collect(request):
    import httpx
    from sqlalchemy import text

    from app.db import SessionLocal, engine
    from app.security.cipher import decrypt_json

    integration_id = str(UUID(request["integration_id"]))
    start = datetime.fromisoformat(request["date_from"])
    end = datetime.now(UTC)
    if start.tzinfo is None or not 0 <= (end - start).total_seconds() <= 86400:
        raise ValueError("Probe window must be within the last 24 hours")
    result = {
        "source": "ml_api_read",
        "integration_id": integration_id,
        "started_at": end.isoformat(),
        "captured_at": None,
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "db_read_only": None,
        "no_token_refresh": True,
        "orders": [],
        "total_eligible": None,
        "limit": 20,
        "calls": [],
        "failures": [],
    }
    try:
        async with SessionLocal() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            result["db_read_only"] = (
                await session.execute(text("SHOW transaction_read_only"))
            ).scalar()
            raw = (
                await session.execute(
                    text(
                        "SELECT credentials FROM davinci.integrations "
                        "WHERE id=:id AND platform='ml' AND status='active' AND archived_at IS NULL"
                    ),
                    {"id": UUID(integration_id)},
                )
            ).scalar_one()
            credentials = decrypt_json(raw)
            token, seller = credentials.get("access_token"), str(credentials.get("user_id", ""))
            await session.rollback()
        if not token or not seller.isdigit():
            raise ValueError("Existing authorization lacks token or seller identity")
        async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:

            async def get(path, params=None):
                if not (
                    path == "/orders/search"
                    or path.startswith("/shipments/")
                    and path[11:].isdigit()
                ):
                    raise ValueError("Outside read allowlist")
                await asyncio.sleep(1.5)
                response = await client.get(
                    "https://api.mercadolibre.com" + path,
                    params=params,
                    headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
                )
                result["calls"].append(
                    {"method": "GET", "path": path, "status": response.status_code}
                )
                if response.status_code != 200:
                    result["failures"].append({"path": path, "status": response.status_code})
                    raise RuntimeError("Provider read failed")
                return response.json()

            data = await get(
                "/orders/search",
                {
                    "seller": seller,
                    "order.date_last_updated.from": start.isoformat(),
                    "order.date_last_updated.to": end.isoformat(),
                    "limit": 20,
                    "offset": 0,
                },
            )
            result["total_eligible"] = data["paging"]["total"]
            for order in data.get("results", [])[:20]:
                if str(order["seller"]["id"]) != seller:
                    raise ValueError("Order belongs to another seller")
                shipment_id = (order.get("shipping") or {}).get("id")
                shipment = await get("/shipments/" + str(shipment_id)) if shipment_id else {}
                result["orders"].append(
                    {
                        "id": str(order["id"]),
                        "status": order["status"],
                        "created_at": order["date_created"],
                        "updated_at": order["date_last_updated"],
                        "currency": order["currency_id"],
                        "shipment": {
                            "status": shipment.get("status"),
                            "logistic_type": shipment.get("logistic_type"),
                        },
                        "items": [
                            {
                                "listing_id": str(item["item"]["id"]),
                                "variation_id": str(item["item"]["variation_id"])
                                if item["item"].get("variation_id") is not None
                                else None,
                                "quantity": str(item["quantity"]),
                                "unit_price": str(item["unit_price"]),
                            }
                            for item in order["order_items"]
                        ],
                    }
                )
    except Exception as exc:
        result["failures"].append({"code": type(exc).__name__})
    finally:
        result["captured_at"] = datetime.now(UTC).isoformat()
        await engine.dispose()
    return result


if __name__ == "__main__":
    request = json.loads(base64.b64decode(sys.argv[1], validate=True))
    print(json.dumps(asyncio.run(collect(request)), ensure_ascii=False))
