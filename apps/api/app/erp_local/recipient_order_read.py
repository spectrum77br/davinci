"""Appended to one channel's order probe and run on the API host, for named orders only.

Read-only. Unlike the presence probe, this returns buyer data, so it runs only for the capture
that stores it. Only the fields the projection uses leave this process.
"""

SHOPEE_DETAIL = "/api/v2/order/get_order_detail"
SHOPEE_BATCH = 50
TIKTOK_DAYS = 7


def shopee_query(path, credentials, settings, query):
    now = int(time.time())  # noqa: F821
    token, shop = credentials["access_token"], int(credentials["shop_id"])
    partner = int(credentials.get("partner_id") or settings.shopee_partner_id)
    secret = credentials.get("partner_key") or settings.shopee_partner_key
    signature = hmac.new(  # noqa: F821
        secret.encode(),
        f"{partner}{path}{now}{token}{shop}".encode(),
        hashlib.sha256,  # noqa: F821
    ).hexdigest()
    return {
        **query,
        "partner_id": partner,
        "timestamp": now,
        "access_token": token,
        "shop_id": shop,
        "sign": signature,
    }


async def body_of(response):
    if response.status_code != 200:
        return None
    try:
        return response.json()
    except ValueError:
        return None


async def recipients_shopee(client, credentials, settings, ids):
    result = {}
    for start in range(0, len(ids), SHOPEE_BATCH):
        params = shopee_query(
            SHOPEE_DETAIL,
            credentials,
            settings,
            {
                "order_sn_list": ",".join(ids[start : start + SHOPEE_BATCH]),
                "response_optional_fields": "recipient_address,buyer_cpf_id",
            },
        )
        body = await body_of(await client.get(BASE + SHOPEE_DETAIL, params=params))  # noqa: F821
        if not body or body.get("error"):
            continue
        for order in (body.get("response") or {}).get("order_list") or []:
            result[str(order["order_sn"])] = {
                "buyer_cpf_id": order.get("buyer_cpf_id"),
                "recipient_address": order.get("recipient_address"),
            }
    return result


async def recipients_ml(client, credentials, _settings, ids):
    headers = {"Authorization": "Bearer " + credentials["access_token"]}
    result = {}
    for identifier in ids:
        order = await body_of(
            await client.get(f"{BASE}/orders/{identifier}", headers=headers)  # noqa: F821
        )
        billing = await body_of(
            await client.get(f"{BASE}/orders/{identifier}/billing_info", headers=headers)  # noqa: F821
        )
        shipment_id = ((order or {}).get("shipping") or {}).get("id")
        if not (billing and shipment_id):
            continue
        shipment = await body_of(
            await client.get(
                f"{BASE}/shipments/{shipment_id}",  # noqa: F821
                headers={**headers, "x-format-new": "true"},
            )
        )
        if shipment:
            result[identifier] = {"billing": billing, "shipment": shipment}
    return result


async def recipients_magalu(client, credentials, _settings, ids):
    headers = {
        "Authorization": "Bearer " + credentials["access_token"],
        "Accept": "application/json",
    }
    result = {}
    for identifier in ids:
        order = await body_of(await client.get(f"{URL}/{identifier}", headers=headers))  # noqa: F821
        if order:
            result[identifier] = {
                "customer": order.get("customer"),
                "deliveries": order.get("deliveries"),
            }
    return result


async def recipients_tiktok(client, credentials, _settings, ids):
    """Its search already carries the recipient, one day at a time."""
    wanted, result = set(ids), {}
    now = int(time.time())  # noqa: F821
    for day in range(TIKTOK_DAYS):
        token = None
        while wanted:
            plan_ = request_plan(  # noqa: F821
                credentials,
                "POST",
                SEARCH,  # noqa: F821
                {"page_token": token} if token else {},
                {
                    "update_time_ge": now - 86400 * (day + 1),
                    "update_time_lt": now - 86400 * day - 3,
                },
                now=now,
            )
            async with client.stream(**plan_) as response:
                code = response.status_code
                payload = await response.aread()
            if code != 200:
                return result
            data = json.loads(payload).get("data") or {}  # noqa: F821
            for order in data.get("orders") or []:
                identifier = str(order.get("id"))
                if identifier in wanted:
                    wanted.discard(identifier)
                    result[identifier] = {
                        "cpf": order.get("cpf"),
                        "cpf_name": order.get("cpf_name"),
                        "recipient_address": order.get("recipient_address"),
                    }
            token = data.get("next_page_token")
            if not token:
                break
    return result


RECIPIENT_READERS = {
    "shopee": recipients_shopee,
    "ml": recipients_ml,
    "magalu": recipients_magalu,
    "tiktok": recipients_tiktok,
}


async def recipients(request):
    import httpx
    from sqlalchemy import text

    from app.config import get_settings
    from app.db import SessionLocal, engine
    from app.security.cipher import decrypt_json

    logging.getLogger("httpx").setLevel(logging.CRITICAL)  # noqa: F821
    logging.getLogger("httpcore").setLevel(logging.CRITICAL)  # noqa: F821
    try:
        async with SessionLocal() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            raw = (
                await session.execute(
                    text(
                        "SELECT credentials FROM davinci.integrations "
                        "WHERE id=CAST(:id AS uuid) AND platform=:platform AND status='active'"
                    ),
                    {"id": request["integration_id"], "platform": request["platform"]},
                )
            ).scalar_one()
            await session.rollback()
        settings = get_settings()
        proxy = settings.magalu_proxy_url if request["platform"] == "magalu" else None
        async with httpx.AsyncClient(
            timeout=25, follow_redirects=False, proxy=proxy or None
        ) as client:
            return await RECIPIENT_READERS[request["platform"]](
                client, decrypt_json(raw), settings, [str(i) for i in request["ids"]]
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    request = json.loads(base64.b64decode(sys.argv[1], validate=True))  # noqa: F821
    print(json.dumps(asyncio.run(recipients(request)), ensure_ascii=False))  # noqa: F821
