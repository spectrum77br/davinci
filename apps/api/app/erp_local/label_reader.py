"""Run where the credentials are: labels and tracking of named sales, read from each store.

Labels only for shipments the store already has arranged, so nothing changes there. The label
carries buyer data and goes back only to the ERP on the same server.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import sys
import time

ML = "https://api.mercadolibre.com"
SHOPEE = "https://partner.shopeemobile.com"
TIKTOK = "https://open-api.tiktokglobalshop.com"


def ml_call(client, c):
    async def call(method, path, headers=None, params=None, binary=False):
        auth = {"Authorization": "Bearer " + c["access_token"], **(headers or {})}
        r = await client.request(method, ML + path, headers=auth, params=params)
        r.raise_for_status()
        return r.content if binary else r.json()

    return call


def shopee_call(client, c, settings):
    partner = int(c.get("partner_id") or settings.shopee_partner_id)
    secret = c.get("partner_key") or settings.shopee_partner_key

    async def call(method, path, params=None, json=None, binary=False):
        now = int(time.time())
        material = f"{partner}{path}{now}{c['access_token']}{int(c['shop_id'])}"
        query = {
            **(params or {}),
            "partner_id": partner,
            "timestamp": now,
            "access_token": c["access_token"],
            "shop_id": int(c["shop_id"]),
            "sign": hmac.new(secret.encode(), material.encode(), hashlib.sha256).hexdigest(),
        }
        r = await client.request(method, SHOPEE + path, params=query, json=json)
        r.raise_for_status()
        if binary and r.headers.get("content-type", "").startswith("application/pdf"):
            return r.content
        body = r.json()
        if body.get("error"):
            raise RuntimeError("shopee:" + str(body.get("error")))
        return body.get("response") or {}

    return call


def tiktok_call(client, c):
    async def call(method, path, params=None):
        query = {
            "app_key": c["app_key"],
            "timestamp": str(int(time.time())),
            "shop_cipher": c["shop_cipher"],
            **(params or {}),
        }
        material = path + "".join(k + query[k] for k in sorted(query))
        secret = c["app_secret"]
        query["sign"] = hmac.new(
            secret.encode(), (secret + material + secret).encode(), hashlib.sha256
        ).hexdigest()
        r = await client.request(
            method, TIKTOK + path, params=query, headers={"x-tts-access-token": c["access_token"]}
        )
        r.raise_for_status()
        body = r.json()
        if body.get("code"):
            raise RuntimeError("tiktok:" + str(body.get("code")))
        return body.get("data") or {}

    return call


async def one(client, channel, c, settings, ref, mode):
    from app.erp_local import channel_labels

    if channel == "ml":
        call = ml_call(client, c)
        if mode == "discounts":
            # Who pays each discount (seller or Mercado Livre): Bling takes off the seller's part.
            return await call("GET", f"/orders/{ref}/discounts")
        if mode == "tracking":
            s = await call("GET", f"/shipments/{ref}", headers={"x-format-new": "true"})
            option = s.get("lead_time") or s.get("shipping_option") or {}
            return {
                "tracking_number": s.get("tracking_number") or "",
                "carrier": (s.get("logistic") or {}).get("type") or s.get("tracking_method") or "",
                "status": s.get("status") or "",
                "substatus": s.get("substatus") or "",
                # What the buyer paid for shipping and its list price: Bling's order total
                # for Mercado Livre carries shipping beyond the products.
                "cost": str(option.get("cost")) if option.get("cost") is not None else "",
                "list_cost": str(option.get("list_cost"))
                if option.get("list_cost") is not None
                else "",
                "base_cost": str(s.get("base_cost")) if s.get("base_cost") is not None else "",
            }
        return await channel_labels.fetch_label("ml", ref, call=call, download_only=True)
    if channel == "shopee":
        call = shopee_call(client, c, settings)
        if mode == "tracking":
            number = await call(
                "GET", "/api/v2/logistics/get_tracking_number", params={"order_sn": ref}
            )
            return {
                "tracking_number": number.get("tracking_number") or "",
                "carrier": "",
                "status": "",
            }
        if mode == "money":
            # Seller and Shopee vouchers, coins and shipping: what Bling's total is made of.
            escrow = await call(
                "GET", "/api/v2/payment/get_escrow_detail", params={"order_sn": ref}
            )
            income = escrow.get("order_income") or {}
            return {k: str(v) for k, v in income.items() if isinstance(v, int | float)}
        return await channel_labels.fetch_label("shopee", ref, call=call, download_only=True)
    call = tiktok_call(client, c)
    detail = await call("GET", "/order/202309/orders", {"ids": ref})
    order = (detail.get("orders") or [{}])[0]
    packages = order.get("packages") or []
    if mode == "money":
        payment = order.get("payment") or {}
        return {k: str(v) for k, v in payment.items() if isinstance(v, str | int | float)}
    if mode == "tracking":
        return {
            "tracking_number": order.get("tracking_number") or "",
            "carrier": order.get("shipping_provider") or "",
            "status": order.get("status") or "",
        }
    if not packages:
        raise RuntimeError("sem pacote")

    async def fetch_url(url):
        r = await client.get(url)
        r.raise_for_status()
        return r.content

    return await channel_labels.fetch_label(
        "tiktok", packages[0]["id"], call=call, fetch_url=fetch_url, download_only=True
    )


async def read(request):
    import httpx
    from sqlalchemy import text

    from app.config import get_settings
    from app.db import SessionLocal, engine
    from app.security.cipher import decrypt_json

    settings, result = get_settings(), {}
    try:
        async with SessionLocal() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            raw = (
                await session.execute(
                    text("SELECT credentials FROM davinci.integrations WHERE id=CAST(:i AS uuid)"),
                    {"i": request["integration_id"]},
                )
            ).scalar_one()
            await session.rollback()
        credentials = decrypt_json(raw)
        async with httpx.AsyncClient(timeout=40) as client:
            for ref in request["refs"][:50]:
                try:
                    got = await one(
                        client,
                        request["platform"],
                        credentials,
                        settings,
                        str(ref),
                        request["mode"],
                    )
                except Exception as error:
                    result[str(ref)] = {
                        "error": str(getattr(error, "code", "") or error or type(error).__name__)[
                            :60
                        ]
                    }
                    continue
                if "content" in got:
                    got = {**got, "content": base64.b64encode(got["content"]).decode()}
                result[str(ref)] = got
    finally:
        await engine.dispose()
    return result


if __name__ == "__main__":
    request = json.loads(base64.b64decode(sys.argv[1], validate=True))
    print(json.dumps(asyncio.run(read(request))))
