"""Labels and tracking for the sales read from the stores, printed in one batch like Bling.

Labels are only downloaded for shipments the store already has arranged. They carry buyer data,
so they are kept and served only where the ERP holds buyers (ERP_RECIPIENT_CAPTURE=1).
"""

import base64
import json
import os
from pathlib import Path

from app.erp_local import native_ingest, recipient_capture
from app.erp_local.core import ERPError, dumps

SCHEMA = """
CREATE TABLE IF NOT EXISTS store_labels (
 channel TEXT NOT NULL, store TEXT NOT NULL, number TEXT NOT NULL, format TEXT NOT NULL,
 content BLOB NOT NULL, provenance TEXT NOT NULL, fetched_at TEXT NOT NULL,
 PRIMARY KEY(channel,store,number));
CREATE TABLE IF NOT EXISTS store_tracking (
 channel TEXT NOT NULL, store TEXT NOT NULL, number TEXT NOT NULL, data TEXT NOT NULL,
 updated_at TEXT NOT NULL, PRIMARY KEY(channel,store,number));
"""
SUPPORTED = {"ml", "shopee", "tiktok"}
MAX_BATCH = 100


def reference(db, channel, store, number):
    """What the store calls this shipment: Mercado Livre the shipment, the others the order."""
    unit = native_ingest._units(db, channel, store).get(number)
    if not unit:
        raise ERPError("label_sale", f"Venda {number} não foi lida desta loja.", 404)
    live = [o for o in unit.values() if not native_ingest._is_cancelled(o)]
    if not live:
        raise ERPError("label_cancelled", f"Venda {number} cancelada na loja.", 409)
    if channel == "ml":
        shipment = live[0].get("shipping_id")
        if not shipment:
            raise ERPError("label_shipment", f"Venda {number} sem envio do Mercado Livre.", 409)
        return str(shipment)
    return str(live[0]["id"])


def host_reader():
    if os.environ.get(recipient_capture.CAPTURE_ENV) != "1":
        raise ERPError("label_closed", "Etiquetas só ficam onde os compradores ficam.", 409)
    script = (Path(__file__).parent / "label_reader.py").read_bytes()

    def read(*, channel, store, refs, mode):
        request = {"platform": channel, "integration_id": store, "refs": refs, "mode": mode}
        return native_ingest.run_on_host(script, request)

    return read


def _fetch(erp, items, reader):
    failures, wanted = {}, {}
    with erp.connection() as db:
        for channel, store, number in items:
            if channel not in SUPPORTED:
                failures[number] = "canal sem etiqueta própria"
                continue
            if db.execute(
                "SELECT 1 FROM store_labels WHERE channel=? AND store=? AND number=?",
                (channel, store, number),
            ).fetchone():
                continue
            try:
                ref = reference(db, channel, store, number)
            except ERPError as error:
                failures[number] = error.message
                continue
            wanted.setdefault((channel, store), {})[ref] = number
    for (channel, store), refs in wanted.items():
        replies = reader(channel=channel, store=store, refs=list(refs), mode="label")
        for ref, number in refs.items():
            got = replies.get(ref) or {"error": "sem resposta"}
            if "content" not in got:
                failures[number] = got.get("error") or "não baixada"
                continue
            with erp.connection() as db:
                db.execute(
                    "INSERT OR REPLACE INTO store_labels VALUES (?,?,?,?,?,?,?)",
                    (
                        channel,
                        store,
                        number,
                        got["format"],
                        base64.b64decode(got["content"]),
                        got["provenance"],
                        erp.now(),
                    ),
                )
    return failures


def batch(erp, items, reader=None):
    """One PDF with every label asked, in the order asked; what could not come is reported."""
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_BATCH:
        raise ERPError("label_batch", f"Selecione de 1 a {MAX_BATCH} vendas.")
    keys = [(str(i["channel"]), str(i["store"]), str(i["number"])) for i in items]
    from app.erp_local import would

    if would.active() and reader is None:
        # Homologation: nothing is asked of the store; each page says what would be printed.
        return would.label_pdf(erp, keys), {}
    failures = _fetch(erp, keys, reader or host_reader())
    import fitz

    merged = fitz.open()
    with erp.connection() as db:
        for channel, store, number in keys:
            row = db.execute(
                "SELECT format,content FROM store_labels WHERE channel=? AND store=? AND number=?",
                (channel, store, number),
            ).fetchone()
            if row and row["format"] == "pdf":
                with fitz.open(stream=row["content"], filetype="pdf") as part:
                    merged.insert_pdf(part)
    if not merged.page_count:
        raise ERPError("label_none", "Nenhuma etiqueta disponível: " + dumps(failures), 409)
    return merged.tobytes(), failures


def tracking(erp, channel, store, number, reader=None):
    with erp.connection() as db:
        ref = reference(db, channel, store, number)
    got = (reader or host_reader())(channel=channel, store=store, refs=[ref], mode="tracking")
    data = got.get(ref) or {}
    if "error" in data:
        raise ERPError(
            "tracking_unavailable", f"A loja não informou o rastreio: {data['error']}", 409
        )
    with erp.connection() as db:
        db.execute(
            "INSERT OR REPLACE INTO store_tracking VALUES (?,?,?,?,?)",
            (channel, store, number, json.dumps(data), erp.now()),
        )
    return data
