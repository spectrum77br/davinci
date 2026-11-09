"""Appended to one channel's order probe and run on the API host. Read-only requests.

Answers whether the channel gives our own credentials what an invoice and a label need. Values
never leave this process: only field paths with present and masked counts are printed.
"""

import hashlib  # noqa: E402
import hmac  # noqa: E402
import time  # noqa: E402

PERSONAL = re.compile(  # noqa: F821
    r"name|nome|document|doc_|cpf|cnpj|tax|address|street|logradouro|number|numero|city|cidade|"
    r"state|uf|zip|postal|cep|phone|fone|email|district|bairro|neighborhood|complement|town|"
    r"region|recipient|receiver|buyer|customer|billing",
    re.IGNORECASE,  # noqa: F821
)
SAMPLE = 3


def walk(value, path, counts):
    if isinstance(value, dict):
        for key, item in value.items():
            walk(item, f"{path}.{key}" if path else str(key), counts)
    elif isinstance(value, list):
        for item in value:
            walk(item, path + "[]", counts)
    elif PERSONAL.search(path) and not isinstance(value, bool):
        row = counts.setdefault(path, {"present": 0, "empty": 0, "masked": 0})
        text = "" if value is None else str(value)
        if not text.strip():
            row["empty"] += 1
        else:
            row["present"] += 1
            if "*" in text:
                row["masked"] += 1


async def fetch(client, method, url, *, headers=None, params=None):
    response = await client.request(method, url, headers=headers, params=params)
    if response.status_code != 200:
        return response.status_code, None
    try:
        return 200, response.json()
    except ValueError:
        return response.status_code, None


async def read_ml(client, credentials, _settings):
    token, seller = credentials["access_token"], credentials["user_id"]
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    code, body = await fetch(
        client,
        "GET",
        BASE + "/orders/search",  # noqa: F821
        headers=headers,
        params={"seller": seller, "sort": "date_desc", "limit": SAMPLE},
    )
    if code != 200:
        return code, []
    result = []
    for order in body.get("results") or []:
        shipment_id = (order.get("shipping") or {}).get("id")
        status_a, billing = await fetch(
            client,
            "GET",
            f"{BASE}/orders/{order.get('id')}/billing_info",  # noqa: F821
            headers=headers,
        )
        status_b, shipment = (0, None)
        if shipment_id:
            status_b, shipment = await fetch(
                client,
                "GET",
                f"{BASE}/shipments/{shipment_id}",  # noqa: F821
                headers={**headers, "x-format-new": "true"},
            )
        result.append(
            {
                "order": {"buyer": order.get("buyer")},
                "billing_info": billing,
                "shipment": shipment,
                "statuses": {"billing_info": status_a, "shipment": status_b},
            }
        )
    return 200, result


def address_shape(orders):
    """How an open address is laid out, in counts and positions: which part holds what, no value."""
    shape = {}

    def add(key, value):
        slot = shape.setdefault(key, {})
        slot[str(value)] = slot.get(str(value), 0) + 1

    for order in orders:
        address = order.get("recipient_address") or {}
        full = str(address.get("full_address") or "")
        if not full or "*" in full:
            continue
        parts = [part.strip() for part in full.split(",")]
        add("parts", len(parts))
        add("number_part", next((i for i, part in enumerate(parts) if part.isdigit()), None))
        for field in ["city", "district", "town", "state", "region"]:
            value = str(address.get(field) or "").strip().casefold()
            add(
                field + "_part",
                next(
                    (i for i, part in enumerate(parts) if value and value in part.casefold()), None
                ),
            )
            add(field + "_len", min(len(value), 3))
        zipcode = "".join(c for c in str(address.get("zipcode") or "") if c.isdigit())
        add("zip_len", len(zipcode))
        add(
            "zip_part",
            next(
                (
                    i
                    for i, part in enumerate(parts)
                    if zipcode and zipcode in "".join(c for c in part if c.isdigit())
                ),
                None,
            ),
        )
        add("cpf_len", len("".join(c for c in str(order.get("buyer_cpf_id") or "") if c.isdigit())))
    return shape


def shopee_params(path, credentials, settings, query):
    """Own signing: the order probe's guard refuses these fields by design, and it should."""
    now = int(time.time())  # noqa: F821
    token = credentials["access_token"]
    shop = int(credentials["shop_id"])
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


async def read_shopee(client, credentials, settings):
    now = int(time.time())  # noqa: F821
    params = shopee_params(
        LIST,  # noqa: F821
        credentials,
        settings,
        {
            "time_range_field": "create_time",
            "time_from": now - 3 * 86400,
            "time_to": now - 3,
            "page_size": SAMPLE,
            "cursor": "",
        },
    )
    code, body = await fetch(client, "GET", BASE + LIST, params=params)  # noqa: F821
    if code != 200 or body.get("error"):
        return code if code != 200 else body.get("error"), []
    ids = [r["order_sn"] for r in (body.get("response") or {}).get("order_list") or []][:SAMPLE]
    if not ids:
        return 200, []
    # Whether masking follows the order status: only status names and counts leave here.
    status_rows = {}
    for status in [
        "INVOICE_PENDING",
        "READY_TO_SHIP",
        "PROCESSED",
        "SHIPPED",
        "COMPLETED",
        "IN_CANCEL",
        "CANCELLED",
    ]:
        listed = shopee_params(
            LIST,  # noqa: F821
            credentials,
            settings,
            {
                "time_range_field": "create_time",
                "time_from": now - 14 * 86400,
                "time_to": now - 3,
                "page_size": 5,
                "cursor": "",
                "order_status": status,
            },
        )
        code, rows = await fetch(client, "GET", BASE + LIST, params=listed)  # noqa: F821
        sns = [r["order_sn"] for r in ((rows or {}).get("response") or {}).get("order_list") or []]
        if not sns:
            status_rows[status] = {"orders": 0}
            continue
        detail = shopee_params(
            DETAIL,  # noqa: F821
            credentials,
            settings,
            {
                "order_sn_list": ",".join(sns),
                "response_optional_fields": "recipient_address,buyer_cpf_id",
            },
        )
        code, reply = await fetch(client, "GET", BASE + DETAIL, params=detail)  # noqa: F821
        found = ((reply or {}).get("response") or {}).get("order_list") or []
        status_rows[status] = {
            "orders": len(found),
            "cpf_masked": sum("*" in str(o.get("buyer_cpf_id") or "*") for o in found),
            "address_masked": sum(
                "*" in str((o.get("recipient_address") or {}).get("full_address") or "*")
                for o in found
            ),
            "name_masked": sum(
                "*" in str((o.get("recipient_address") or {}).get("name") or "*") for o in found
            ),
            "shape": address_shape(found),
        }
    params = shopee_params(
        DETAIL,  # noqa: F821
        credentials,
        settings,
        {
            "order_sn_list": ",".join(ids),
            "response_optional_fields": "recipient_address,buyer_cpf_id,buyer_username,"
            "invoice_data,shipping_carrier,checkout_shipping_carrier",
        },
    )
    code, body = await fetch(client, "GET", BASE + DETAIL, params=params)  # noqa: F821
    if code != 200 or body.get("error"):
        return code if code != 200 else body.get("error"), []
    orders = (body.get("response") or {}).get("order_list") or []
    invoice_path = "/api/v2/order/get_buyer_invoice_info"
    response = await client.post(
        BASE + invoice_path,  # noqa: F821
        params=shopee_params(invoice_path, credentials, settings, {}),
        json={"queries": [{"order_sn": i} for i in ids]},
    )
    invoice = response.json() if response.status_code == 200 else {}
    orders.append({"status_summary": {"recipient_by_status": status_rows}})
    orders.append(
        {
            "buyer_invoice_http": response.status_code,
            "buyer_invoice_error": invoice.get("error"),
            "buyer_invoice": invoice.get("response"),
        }
    )
    return 200, orders


async def read_tiktok(client, credentials, _settings):
    now = int(time.time())  # noqa: F821
    plan_ = request_plan(  # noqa: F821
        credentials,
        "POST",
        SEARCH,  # noqa: F821
        {},
        {"update_time_ge": now - 86400, "update_time_lt": now - 3},
        now=now,
    )
    async with client.stream(**plan_) as response:
        code = response.status_code
        payload = await response.aread()
    if code != 200:
        return code, []
    body = json.loads(payload)  # noqa: F821
    if body.get("code"):
        return body.get("code"), []
    return 200, ((body.get("data") or {}).get("orders") or [])[:SAMPLE]


async def read_amazon(client, credentials, _settings):
    read_plan = plan(credentials, datetime.now(UTC))  # noqa: F821
    params = {**read_plan.params, "includedData": "RECIPIENT,BUYER", "maxResultsPerPage": SAMPLE}
    code, body = await fetch(client, "GET", read_plan.url, headers=read_plan.headers, params=params)
    if code != 200:
        return code, []
    orders = (body.get("orders") or [])[:SAMPLE]
    result = []
    for order in orders:
        identifier_ = order.get("orderId")
        paths = [f"/orders/v0/orders/{identifier_}/{name}" for name in ["address", "buyerInfo"]]
        grant = await client.post(
            BASE + "/tokens/2021-03-01/restrictedDataToken",  # noqa: F821
            headers=read_plan.headers,
            json={"restrictedResources": [{"method": "GET", "path": p} for p in paths]},
        )
        row = {"list": order, "restricted_token_http": grant.status_code}
        # The same order through the route that names which personal data it wants.
        whole = f"/orders/v0/orders/{identifier_}"
        named = await client.post(
            BASE + "/tokens/2021-03-01/restrictedDataToken",  # noqa: F821
            headers=read_plan.headers,
            json={
                "restrictedResources": [
                    {
                        "method": "GET",
                        "path": whole,
                        "dataElements": ["buyerInfo", "shippingAddress"],
                    }
                ]
            },
        )
        row["named_token_http"] = named.status_code
        if named.status_code == 200:
            status, reply = await fetch(
                client,
                "GET",
                BASE + whole,  # noqa: F821
                headers={
                    "x-amz-access-token": named.json().get("restrictedDataToken"),
                    "Accept": "application/json",
                },
            )
            row["named_http"] = status
            row["named"] = reply
        if grant.status_code == 200:
            token = grant.json().get("restrictedDataToken")
            headers = {"x-amz-access-token": token, "Accept": "application/json"}
            for name, path in zip(["address", "buyerInfo"], paths, strict=True):
                status, reply = await fetch(client, "GET", BASE + path, headers=headers)  # noqa: F821
                row[name + "_http"] = status
                row[name] = reply
        result.append(row)
    return 200, result


async def read_magalu(client, credentials, _settings):
    code, body = await fetch(
        client,
        "GET",
        URL,  # noqa: F821
        headers={
            "Authorization": "Bearer " + credentials["access_token"],
            "Accept": "application/json",
        },
        params={"_limit": PAGE_SIZE, "_offset": 0},  # noqa: F821
    )
    if code != 200:
        return code, []
    return 200, (body.get("results") or [])[:SAMPLE]


async def read_carrefour(client, credentials, _settings):
    now = int(time.time())  # noqa: F821
    params, headers = parameters(  # noqa: F821
        credentials,
        ORDERS,  # noqa: F821
        start=datetime.fromtimestamp(now - 86400, UTC).isoformat(),  # noqa: F821
        end=datetime.fromtimestamp(now - 3, UTC).isoformat(),  # noqa: F821
        offset=0,
    )
    code, body = await fetch(client, "GET", BASE + ORDERS, headers=headers, params=params)  # noqa: F821
    if code != 200:
        return code, []
    return 200, (body.get("orders") or [])[:SAMPLE]


READERS = {
    "ml": read_ml,
    "shopee": read_shopee,
    "tiktok": read_tiktok,
    "amazon": read_amazon,
    "magalu": read_magalu,
    "carrefour": read_carrefour,
}


async def presence(request):
    import httpx
    from sqlalchemy import text

    from app.config import get_settings
    from app.db import SessionLocal, engine
    from app.security.cipher import decrypt_json

    logging.getLogger("httpx").setLevel(logging.CRITICAL)  # noqa: F821
    logging.getLogger("httpcore").setLevel(logging.CRITICAL)  # noqa: F821
    report = {"platform": request["platform"], "accounts": []}
    try:
        async with SessionLocal() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT id,credentials FROM davinci.integrations "
                            "WHERE id=ANY(CAST(:ids AS uuid[])) AND status='active'"
                        ),
                        {"ids": request["integration_ids"]},
                    )
                )
                .mappings()
                .all()
            )
            await session.rollback()
        settings = get_settings()
        # Magalu only answers through the server's own proxy.
        proxy = settings.magalu_proxy_url if request["platform"] == "magalu" else None
        async with httpx.AsyncClient(
            timeout=25, follow_redirects=False, proxy=proxy or None
        ) as client:
            for row in rows:
                entry = {"integration_id": str(row["id"]), "http": None, "orders": 0, "fields": {}}
                try:
                    credentials = decrypt_json(row["credentials"])
                    code, orders = await READERS[request["platform"]](client, credentials, settings)
                    entry["http"] = code
                    entry["orders"] = len(orders)
                    for order in orders:
                        walk(order, "", entry["fields"])
                        if "status_summary" in order:
                            entry["by_status"] = order["status_summary"]["recipient_by_status"]
                        if order.get("buyer_invoice_error") is not None:
                            entry["invoice_error"] = order["buyer_invoice_error"]
                except Exception as exc:
                    # Never print provider bodies, headers or credentials.
                    entry["error"] = type(exc).__name__
                report["accounts"].append(entry)
    finally:
        await engine.dispose()
    return report


request = json.loads(base64.b64decode(sys.argv[1], validate=True))  # noqa: F821
print(json.dumps(asyncio.run(presence(request)), ensure_ascii=False))  # noqa: F821
