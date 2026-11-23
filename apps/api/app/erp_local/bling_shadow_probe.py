"""Bling side of the shadow check: a few GETs to Bling's API, read only, on the server.

Runs where DaVinci's Bling credentials are (the ERP worker on the server, or the API container
through SSH). Only GET, only the paths below, never a token refresh: with the token about to expire
the round is skipped and DaVinci's own refresh fixes it. Each call waits its turn in DaVinci's own
Bling queue (the same Redis slot), so DaVinci keeps its rate. Contacts, orders and invoices are not
on the list, so no buyer data travels. Bling's open payables come too (bling_payables): the list,
each bill and its supplier by id, only the ids those bills point to.
"""

import asyncio
import base64
import json
import logging
import re
import sys
import time
from datetime import UTC, datetime

SOURCE = "bling_shadow_v1_read"
MAX_CALLS = 40
ALLOWED = tuple(
    re.compile(p)
    for p in (
        r"/situacoes/modulos",
        r"/situacoes/modulos/\d{1,12}",
        r"/depositos",
        r"/canais-venda",
        r"/categorias/produtos",
        r"/produtos",
        r"/produtos/\d{1,15}",
        r"/estoques/saldos",
        r"/contas/pagar",
        r"/contas/pagar/\d{1,15}",
        r"/contatos/\d{1,15}",
    )
)
PARAMS = {"pagina", "limite", "codigo", "idsProdutos[]", "situacoes[]"}
STATES = {"read_ok", "token_expired", "configuration_missing", "provider_unavailable"}


def checked(calls):
    """The calls as asked, or ValueError for anything outside the list."""
    if not isinstance(calls, list) or not 1 <= len(calls) <= MAX_CALLS:
        raise ValueError
    clean = []
    for call in calls:
        if not isinstance(call, dict) or set(call) != {"path", "params"}:
            raise ValueError
        path, params = call["path"], call["params"]
        if not isinstance(path, str) or not any(p.fullmatch(path) for p in ALLOWED):
            raise ValueError
        if not isinstance(params, list) or len(params) > 25:
            raise ValueError
        for pair in params:
            if (
                not isinstance(pair, list)
                or len(pair) != 2
                or pair[0] not in PARAMS
                or not isinstance(pair[1], str)
                or not 0 < len(pair[1]) <= 120
            ):
                raise ValueError
        clean.append({"path": path, "params": [list(p) for p in params]})
    return clean


def report(state, results=()):
    return {
        "version": 1,
        "source": SOURCE,
        "read_only": "on",
        "no_token_refresh": True,
        "captured_at": datetime.now(UTC).isoformat(),
        "state": state,
        "results": list(results),
    }


async def read(request):
    try:
        calls = checked(request.get("calls"))
    except (ValueError, AttributeError):
        return report("configuration_missing")
    import httpx
    from sqlalchemy import text

    from app.db import SessionLocal, engine
    from app.security.cipher import decrypt_json
    from app.services.marketplaces.bling import BLING_API_BASE, _acquire_bling_rate_slot

    logging.getLogger("httpx").setLevel(logging.CRITICAL)
    try:
        async with SessionLocal() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            row = (
                await session.execute(
                    text(
                        "SELECT credentials FROM davinci.integrations "
                        "WHERE platform='bling' LIMIT 1"
                    )
                )
            ).first()
            await session.rollback()
        if row is None:
            return report("configuration_missing")
        try:
            creds = decrypt_json(row[0])
        except Exception:  # noqa: BLE001 - unreadable credentials are a configuration problem
            return report("configuration_missing")
        token, expires = creds.get("access_token"), creds.get("expires_at")
        if not token or (expires and float(expires) < time.time() + 120):
            return report("token_expired")
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        results = []
        async with httpx.AsyncClient(timeout=30.0) as client:
            for call in calls:
                await _acquire_bling_rate_slot()
                response = await client.get(
                    BLING_API_BASE + call["path"], headers=headers, params=call["params"]
                )
                if response.status_code == 401:
                    return report("token_expired")
                try:
                    body = response.json()
                except ValueError:
                    body = None
                results.append({"status": response.status_code, "body": body})
        return report("read_ok", results)
    except Exception:  # noqa: BLE001 - any failure is reported, never retried with a refresh
        return report("provider_unavailable")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    request = json.loads(base64.b64decode(sys.argv[1], validate=True))
    print(json.dumps(asyncio.run(read(request)), ensure_ascii=False))
