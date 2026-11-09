"""Label steps against a fake store: order of calls, and closed until explicitly opened."""

import asyncio

import pytest

from app.erp_local import channel_labels
from app.erp_local.core import ERPError


class Store:
    def __init__(self, replies):
        self.replies, self.calls = replies, []

    async def __call__(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        reply = self.replies[path]
        return reply.pop(0) if isinstance(reply, list) else reply


def go(awaitable):
    return asyncio.run(awaitable)


async def no_wait(_seconds):
    return None


@pytest.fixture
def open_writes(monkeypatch):
    monkeypatch.setenv(channel_labels.WRITES_ENV, "1")


def test_every_channel_stays_closed_by_default(monkeypatch):
    monkeypatch.delenv(channel_labels.WRITES_ENV, raising=False)
    store = Store({})
    for channel in ["ml", "shopee", "tiktok"]:
        with pytest.raises(ERPError, match="desligado"):
            go(channel_labels.fetch_label(channel, "1", call=store))
    assert store.calls == []


@pytest.mark.parametrize("channel", ["amazon", "magalu", "carrefour", "temu"])
def test_channels_without_connector_refuse_instead_of_guessing(open_writes, channel):
    with pytest.raises(ERPError, match="conector"):
        go(channel_labels.fetch_label(channel, "1", call=Store({})))


def test_ml_checks_the_shipment_before_printing(open_writes):
    store = Store(
        {
            "/shipments/9": {"status": "ready_to_ship", "logistic": {"mode": "me2"}},
            "/shipment_labels": b"%PDF",
        }
    )
    label = go(channel_labels.fetch_label("ml", 9, call=store, fmt="zpl"))
    assert label["content"] == b"%PDF" and label["format"] == "zpl"
    assert store.calls[1][2]["params"] == {"shipment_ids": "9", "response_type": "zpl2"}
    refused = Store({"/shipments/9": {"status": "delivered"}})
    with pytest.raises(ERPError, match="pronto"):
        go(channel_labels.fetch_label("ml", 9, call=refused))
    assert len(refused.calls) == 1


def test_shopee_runs_the_documented_sequence_and_waits_for_the_document(open_writes):
    store = Store(
        {
            "/api/v2/logistics/ship_order": {},
            "/api/v2/logistics/get_tracking_number": {"tracking_number": "BR1"},
            "/api/v2/logistics/create_shipping_document": {},
            "/api/v2/logistics/get_shipping_document_result": [
                {"result_list": [{"status": "PROCESSING"}]},
                {"result_list": [{"status": "READY"}]},
            ],
            "/api/v2/logistics/download_shipping_document": b"%PDF",
        }
    )
    label = go(channel_labels.fetch_label("shopee", "SN1", call=store, sleep=no_wait))
    assert label["provenance"] == "shopee_shipping_document"
    assert [c[1].rsplit("/", 1)[-1] for c in store.calls] == [
        "ship_order",
        "get_tracking_number",
        "create_shipping_document",
        "get_shipping_document_result",
        "get_shipping_document_result",
        "download_shipping_document",
    ]


def test_shopee_failure_and_missing_tracking_stop_without_a_label(open_writes):
    no_tracking = Store(
        {"/api/v2/logistics/ship_order": {}, "/api/v2/logistics/get_tracking_number": {}}
    )
    with pytest.raises(ERPError, match="rastreio"):
        go(channel_labels.fetch_label("shopee", "SN1", call=no_tracking, sleep=no_wait))
    failed = Store(
        {
            "/api/v2/logistics/ship_order": {},
            "/api/v2/logistics/get_tracking_number": {"tracking_number": "BR1"},
            "/api/v2/logistics/create_shipping_document": {},
            "/api/v2/logistics/get_shipping_document_result": {
                "result_list": [{"status": "FAILED"}]
            },
        }
    )
    with pytest.raises(ERPError, match="recusou"):
        go(channel_labels.fetch_label("shopee", "SN1", call=failed, sleep=no_wait))


def test_tiktok_downloads_the_document_it_was_given(open_writes):
    store = Store(
        {"/fulfillment/202309/packages/P1/shipping_documents": {"doc_url": "https://doc.test/x"}}
    )

    async def fetch_url(url):
        assert url == "https://doc.test/x"
        return b"%PDF"

    label = go(channel_labels.fetch_label("tiktok", "P1", call=store, fetch_url=fetch_url))
    assert label["content"] == b"%PDF"


def test_shopee_already_arranged_order_only_downloads(monkeypatch):
    import asyncio

    monkeypatch.setenv(channel_labels.WRITES_ENV, "1")
    calls = []

    async def call(method, path, **kw):
        calls.append(path)
        if path.endswith("get_tracking_number"):
            return {"tracking_number": "BR1"}
        if path.endswith("get_shipping_document_result"):
            return {"result_list": [{"status": "READY"}]}
        return b"%PDF" if kw.get("binary") else {}

    async def nap(_):
        return None

    result = asyncio.run(
        channel_labels.fetch_label("shopee", "SN1", call=call, arrange=False, sleep=nap)
    )
    assert result["content"] == b"%PDF" and not any("ship_order" in p for p in calls)


def test_ml_label_not_yet_printed_is_left_alone_when_asked(monkeypatch):
    import asyncio

    monkeypatch.setenv(channel_labels.WRITES_ENV, "1")
    downloads = []

    async def call(method, path, **kw):
        if path.startswith("/shipments/"):
            return {"status": "ready_to_ship", "substatus": "ready_to_print"}
        downloads.append(path)
        return b"%PDF"

    with pytest.raises(ERPError, match="não impressa"):
        asyncio.run(channel_labels.fetch_label("ml", "123", call=call, only_printed=True))
    assert downloads == []
