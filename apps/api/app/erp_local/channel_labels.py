"""Shipping labels issued by the stores themselves, kept as the original files.

Requesting a label changes the shipment at the store (ML marks it printed, Shopee arranges the
pickup), so every call here stays closed until ERP_CHANNEL_WRITES is set where the credentials
live. Endpoints follow each store's public documentation and still need account homologation.
Signing and tokens stay with the caller, through the `call` function handed in.
"""

import asyncio
import os

from app.erp_local.core import ERPError

WRITES_ENV = "ERP_CHANNEL_WRITES"
SUPPORTED = {"ml", "shopee", "tiktok"}
FORMATS = {"pdf", "zpl"}


def writes_open():
    return os.environ.get(WRITES_ENV) == "1"


def _guard(channel, fmt, *, download_only=False):
    if channel not in SUPPORTED:
        raise ERPError("label_channel", "Etiqueta deste canal ainda não tem conector próprio.", 409)
    if fmt not in FORMATS:
        raise ERPError("label_format", "Escolha PDF ou ZPL.")
    # Downloading the label of a shipment the store already has arranged changes nothing there.
    if not download_only and not writes_open():
        raise ERPError(
            "label_writes_closed",
            "Gerar etiqueta altera o envio na loja e segue desligado até a homologação da conta.",
            409,
        )


async def ml_label(call, shipment_id, fmt="pdf", *, only_printed=False):
    shipment = await call("GET", f"/shipments/{shipment_id}", headers={"x-format-new": "true"})
    if shipment.get("status") != "ready_to_ship":
        raise ERPError("label_shipment_state", "O envio não está pronto para etiqueta.", 409)
    # Downloading a label not yet printed marks it printed at the store.
    if only_printed and shipment.get("substatus") != "printed":
        raise ERPError("label_not_printed", "Etiqueta ainda não impressa pela operação.", 409)
    if (shipment.get("logistic") or {}).get("mode") == "me1":
        raise ERPError("label_channel", "Envio sem etiqueta do Mercado Livre.", 409)
    body = await call(
        "GET",
        "/shipment_labels",
        params={
            "shipment_ids": str(shipment_id),
            "response_type": "zpl2" if fmt == "zpl" else "pdf",
        },
        binary=True,
    )
    return {"format": fmt, "content": body, "provenance": "ml_shipment_labels"}


async def shopee_label(
    call, order_sn, *, package_number=None, pickup=None, arrange=True, sleep=asyncio.sleep
):
    order = {"order_sn": order_sn}
    if package_number:
        order["package_number"] = package_number
    shipment = {**order}
    if pickup:
        shipment["pickup"] = pickup
    else:
        shipment["dropoff"] = {}
    # Arranging chooses pickup or drop-off at the store; an arranged order only needs the file.
    if arrange:
        await call("POST", "/api/v2/logistics/ship_order", json=shipment)
    tracking = await call("GET", "/api/v2/logistics/get_tracking_number", params=order)
    number = tracking.get("tracking_number")
    if not number:
        raise ERPError("label_pending", "A loja ainda não gerou o rastreio; tente novamente.", 409)
    item = {**order, "tracking_number": number, "shipping_document_type": "NORMAL_AIR_WAYBILL"}
    await call("POST", "/api/v2/logistics/create_shipping_document", json={"order_list": [item]})
    for _ in range(5):
        result = await call(
            "POST", "/api/v2/logistics/get_shipping_document_result", json={"order_list": [order]}
        )
        rows = result.get("result_list") or []
        status = rows[0].get("status") if rows else None
        if status == "READY":
            break
        if status == "FAILED":
            raise ERPError("label_failed", "A loja recusou o documento de envio.", 409)
        await sleep(2)
    else:
        raise ERPError("label_pending", "Documento ainda em geração na loja; tente novamente.", 409)
    body = await call(
        "POST",
        "/api/v2/logistics/download_shipping_document",
        json={"shipping_document_type": "NORMAL_AIR_WAYBILL", "order_list": [order]},
        binary=True,
    )
    return {"format": "pdf", "content": body, "provenance": "shopee_shipping_document"}


async def tiktok_label(call, package_id, *, fetch_url):
    document = await call(
        "GET",
        f"/fulfillment/202309/packages/{package_id}/shipping_documents",
        params={"document_type": "SHIPPING_LABEL", "document_size": "A6"},
    )
    url = document.get("doc_url")
    if not url:
        raise ERPError("label_pending", "A loja ainda não gerou a etiqueta; tente novamente.", 409)
    return {
        "format": "pdf",
        "content": await fetch_url(url),
        "provenance": "tiktok_shipping_documents",
    }


async def fetch_label(channel, reference, *, call, fmt="pdf", download_only=False, **options):
    _guard(channel, fmt, download_only=download_only)
    if download_only and channel == "ml":
        options["only_printed"] = True
    if download_only and channel == "shopee":
        options["arrange"] = False
    if channel == "ml":
        return await ml_label(call, reference, fmt, **options)
    if channel == "shopee":
        return await shopee_label(call, reference, **options)
    return await tiktok_label(call, reference, **options)
