"""Portable one-time collector, executed over stdin in the existing API container.

Only GET requests are implemented. Credentials stay in the existing server's
memory. OAuth refresh and business writes are deliberately absent. The shared
rate gate is used so this reader cooperates with the running application.
"""

import asyncio
import base64
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID


async def collect(request):
    import httpx
    from sqlalchemy import text

    from app.db import SessionLocal, engine
    from app.redis_client import redis
    from app.security.cipher import decrypt_json
    from app.services.marketplaces.bling import _acquire_bling_rate_slot

    integration_id = str(UUID(request["integration_id"]))
    ids = sorted(set(request["product_ids"]), key=int)
    structure_ids = sorted(set(request["structure_ids"]), key=int)
    minimum_interval = max(2.0, min(10.0, float(request.get("minimum_interval", 2.0))))
    if (
        len(ids) > 10000
        or len(structure_ids) > 100
        or any(not isinstance(i, str) or not i.isdigit() for i in ids + structure_ids)
    ):
        raise ValueError("Invalid collection scope")
    result = {
        "source": "bling_api_read",
        "started_at": datetime.now(UTC).isoformat(),
        "captured_at": None,
        "db_read_only": None,
        "no_token_refresh": True,
        "balances": [],
        "deposits": [],
        "structures": [],
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
                        "WHERE id=:id AND platform='bling'"
                    ),
                    {"id": UUID(integration_id)},
                )
            ).scalar_one()
            token = decrypt_json(raw).get("access_token")
            await session.rollback()
        if not token:
            raise ValueError("No existing access token")
        async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:

            async def get(path, params=None):
                if not (
                    path in {"/depositos", "/estoques/saldos"}
                    or path.startswith("/produtos/")
                    and path[10:].isdigit()
                ):
                    raise ValueError("Path outside the read allowlist")
                await _acquire_bling_rate_slot()
                await asyncio.sleep(minimum_interval)
                response = await client.get(
                    "https://api.bling.com.br/Api/v3" + path,
                    headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
                    params=params,
                )
                result["calls"].append(
                    {"method": "GET", "path": path, "status": response.status_code}
                )
                if response.status_code != 200:
                    result["failures"].append({"path": path, "status": response.status_code})
                    raise RuntimeError("Read stopped on HTTP " + str(response.status_code))
                return response.json()["data"]

            def numeric(value):
                if value is None:
                    return None
                number = Decimal(str(value))
                if not number.is_finite():
                    raise ValueError("Non-finite balance")
                return str(number)

            for page in range(1, 11):
                depots = await get("/depositos", {"pagina": page, "limite": 100})
                result["deposits"].extend(
                    {
                        "id": str(d["id"]),
                        "name": d["descricao"],
                        "active": d["situacao"] == 1,
                        "default": d["padrao"],
                        "excluded_from_total": d["desconsiderarSaldo"],
                    }
                    for d in depots
                )
                if len(depots) < 100:
                    break
            for start in range(0, len(ids), 100):
                balances = await get(
                    "/estoques/saldos", [("idsProdutos[]", i) for i in ids[start : start + 100]]
                )
                for b in balances:
                    result["balances"].append(
                        {
                            "id": str(b["produto"]["id"]),
                            "physical": numeric(b.get("saldoFisicoTotal")),
                            "available": numeric(b.get("saldoVirtualTotal")),
                            "deposits": [
                                {
                                    "id": str(d["id"]),
                                    "physical": numeric(d.get("saldoFisico")),
                                    "available": numeric(d.get("saldoVirtual")),
                                }
                                for d in b.get("depositos", [])
                            ],
                        }
                    )
            for identifier in structure_ids:
                p = await get("/produtos/" + identifier)
                parts = p.get("estrutura", {}).get("componentes", [])
                result["structures"].append(
                    {
                        "id": identifier,
                        "sku": p.get("codigo"),
                        "name": p.get("nome"),
                        "format": p.get("formato"),
                        "status": p.get("situacao"),
                        "components": [
                            {"id": str(c["produto"]["id"]), "quantity": numeric(c["quantidade"])}
                            for c in parts
                        ],
                    }
                )
    except Exception as exc:
        # Never print provider bodies, headers, credentials or connection strings.
        result["failures"].append({"code": type(exc).__name__})
    finally:
        result["captured_at"] = datetime.now(UTC).isoformat()
        await engine.dispose()
        await redis.aclose()
    return result


if __name__ == "__main__":
    payload = json.loads(base64.b64decode(sys.argv[1], validate=True))
    print(json.dumps(asyncio.run(collect(payload)), ensure_ascii=False))
