"""Label batch and tracking with a fake store reader: nothing reaches the network."""

import base64
import json

import fitz
import pytest

from app.erp_local import store_labels
from app.erp_local.core import ERPError


def pdf(text):
    doc = fitz.open()
    doc.new_page().insert_text((40, 40), text)
    return doc.tobytes()


def observe(erp, channel, store, external, *, status="paid", shipping_id=None):
    order = {"id": external, "status": status, "shipping_id": shipping_id, "items": []}
    with erp.connection() as db:
        db.execute("PRAGMA foreign_keys=OFF")
        columns = [r[1] for r in db.execute("PRAGMA table_info(native_order_observations)")]
        values = {
            "id": external,
            "channel": channel,
            "store": store,
            "external_id": external,
            "updated_at": "2026-11-09",
            "captured_at": "2026-11-09",
            "data": json.dumps(order),
        }
        db.execute(
            f"INSERT INTO native_order_observations({','.join(columns)}) "  # noqa: S608
            f"VALUES ({','.join('?' * len(columns))})",
            [values.get(c, "x") for c in columns],
        )


class Reader:
    def __init__(self, replies):
        self.replies, self.calls = replies, []

    def __call__(self, *, channel, store, refs, mode):
        self.calls.append((channel, refs, mode))
        return {ref: self.replies.get(ref, {"error": "label_not_printed"}) for ref in refs}


def label(text):
    return {"format": "pdf", "content": base64.b64encode(pdf(text)).decode(), "provenance": "x"}


def test_batch_merges_in_order_and_asks_ml_by_shipment(erp):
    observe(erp, "ml", "c", "M1", shipping_id=777)
    observe(erp, "shopee", "s", "S1", status="PROCESSED")
    reader = Reader({"777": label("um"), "S1": label("dois")})
    items = [
        {"channel": "ml", "store": "c", "number": "M1"},
        {"channel": "shopee", "store": "s", "number": "S1"},
    ]
    content, failures = store_labels.batch(erp, items, reader)
    merged = fitz.open(stream=content, filetype="pdf")
    assert merged.page_count == 2 and failures == {}
    assert ("ml", ["777"], "label") in reader.calls
    again, _ = store_labels.batch(erp, items, Reader({}))
    assert fitz.open(stream=again, filetype="pdf").page_count == 2


def test_missing_labels_are_reported_and_none_at_all_is_refused(erp):
    observe(erp, "ml", "c", "M1", shipping_id=1)
    observe(erp, "tiktok", "t", "T1", status="CANCELLED")
    items = [
        {"channel": "ml", "store": "c", "number": "M1"},
        {"channel": "amazon", "store": "a", "number": "A1"},
    ]
    with pytest.raises(ERPError, match="Nenhuma etiqueta"):
        store_labels.batch(erp, items, Reader({}))
    observe(erp, "shopee", "s", "S1", status="PROCESSED")
    _, failures = store_labels.batch(
        erp,
        [*items, {"channel": "shopee", "store": "s", "number": "S1"}],
        Reader({"S1": label("x")}),
    )
    assert set(failures) == {"M1", "A1"}


def test_tracking_is_kept_and_errors_are_explained(erp):
    observe(erp, "shopee", "s", "S1", status="PROCESSED")
    got = store_labels.tracking(
        erp, "shopee", "s", "S1", Reader({"S1": {"tracking_number": "BR9"}})
    )
    assert got["tracking_number"] == "BR9"
    with pytest.raises(ERPError, match="rastreio"):
        store_labels.tracking(erp, "shopee", "s", "S1", Reader({}))


def test_labels_stay_closed_where_buyers_are_not_kept(monkeypatch):
    monkeypatch.delenv("ERP_RECIPIENT_CAPTURE", raising=False)
    with pytest.raises(ERPError, match="compradores"):
        store_labels.host_reader()
