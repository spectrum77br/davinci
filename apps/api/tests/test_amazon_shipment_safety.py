"""Amazon-only dispatch safety: a seller's Shipped flag is not a physical scan.

The current Amazon API response must allow the Correios fallback explicitly.
Failures, contradictory states and missing information never authorize it.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import marketplace_shipment_check as sweep


@pytest.fixture
def pending_amazon():
    return SimpleNamespace(
        bling_id=99880101,
        numero="301695",
        numeroloja="701-1111111-2222222",
        loja="123",
        marketplace_ship_deadline=None,
    )


def _status(**overrides):
    return {
        "order_status": "Shipped",
        "easyship_status": None,
        "fulfillment_channel": "MFN",
        "last_update_date": "2026-10-07T16:00:00Z",
        **overrides,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [403, 404, 429, 500, 503])
async def test_failed_amazon_read_never_falls_back_to_correios(pending_amazon, code):
    request = httpx.Request("GET", "https://amazon.test/orders/one")
    response = httpx.Response(code, request=request)
    client = SimpleNamespace(
        get_order_status=AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "unavailable",
                request=request,
                response=response,
            )
        )
    )
    own_shipping = {}
    result = await sweep._amazon_shipped_for(
        client,
        pending_amazon,
        own_shipping_orders=own_shipping,
    )
    assert result is None and own_shipping == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [None, {}, {"order_status": "Shipped"}])
async def test_missing_amazon_response_cannot_promote(pending_amazon, response):
    client = SimpleNamespace(get_order_status=AsyncMock(return_value=response))
    own_shipping = {}
    result = await sweep._amazon_shipped_for(
        client,
        pending_amazon,
        own_shipping_orders=own_shipping,
    )
    assert result is None and own_shipping == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "order_status", ["Unshipped", "PartiallyShipped", "Canceled", "Pending", "Unknown"]
)
async def test_not_fully_shipped_amazon_order_never_uses_correios(pending_amazon, order_status):
    client = SimpleNamespace(
        get_order_status=AsyncMock(return_value=_status(order_status=order_status))
    )
    own_shipping = {}
    assert (
        await sweep._amazon_shipped_for(
            client,
            pending_amazon,
            own_shipping_orders=own_shipping,
        )
        is None
    )
    assert own_shipping == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "easyship", ["PendingPickUp", "PendingDropOff", "LabelCanceled", "Unknown"]
)
async def test_pending_or_unknown_dba_does_not_use_correios(pending_amazon, easyship):
    client = SimpleNamespace(
        get_order_status=AsyncMock(return_value=_status(easyship_status=easyship))
    )
    own_shipping = {}
    assert (
        await sweep._amazon_shipped_for(
            client,
            pending_amazon,
            own_shipping_orders=own_shipping,
        )
        is None
    )
    assert own_shipping == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", [None, "", "UNKNOWN"])
async def test_unknown_fulfillment_never_uses_correios(pending_amazon, channel):
    client = SimpleNamespace(
        get_order_status=AsyncMock(return_value=_status(fulfillment_channel=channel))
    )
    own_shipping = {}
    assert (
        await sweep._amazon_shipped_for(
            client,
            pending_amazon,
            own_shipping_orders=own_shipping,
        )
        is None
    )
    assert own_shipping == {}


@pytest.mark.asyncio
async def test_only_current_mfn_shipped_without_easyship_allows_tracking_check(pending_amazon):
    client = SimpleNamespace(get_order_status=AsyncMock(return_value=_status()))
    own_shipping = {}
    assert (
        await sweep._amazon_shipped_for(
            client,
            pending_amazon,
            own_shipping_orders=own_shipping,
        )
        is None
    )  # Amazon seller Shipped by itself is NEVER a confirmation.
    assert pending_amazon.bling_id in own_shipping
    client.get_order_status.assert_awaited_once_with(pending_amazon.numeroloja)


@pytest.mark.asyncio
async def test_timeout_does_not_reuse_old_amazon_status(pending_amazon):
    client = SimpleNamespace(get_order_status=AsyncMock(side_effect=httpx.ReadTimeout("timeout")))
    own_shipping = {}
    assert (
        await sweep._amazon_shipped_for(
            client,
            pending_amazon,
            own_shipping_orders=own_shipping,
        )
        is None
    )
    assert own_shipping == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "platform, client_name, shipped, pending",
    [
        ("shopee", "ShopeeClient", "SHIPPED", "READY_TO_SHIP"),
        ("tiktok", "TikTokClient", "IN_TRANSIT", "AWAITING_COLLECTION"),
    ],
)
async def test_shopee_and_tiktok_keep_existing_decisions_without_amazon_tracking(
    pending_amazon,
    monkeypatch,
    platform,
    client_name,
    shipped,
    pending,
):
    from app.models import IntegrationPlatform

    integration = SimpleNamespace(platform=IntegrationPlatform(platform), credentials=b"test")
    second = SimpleNamespace(bling_id=99880102, numeroloja="other", marketplace_ship_deadline=None)
    client = SimpleNamespace(
        get_order_status_map=AsyncMock(
            return_value={
                pending_amazon.numeroloja: {"status": shipped, "update_time": 1_791_388_800},
                "other": {"status": pending, "update_time": 1_791_388_800},
            }
        )
    )
    monkeypatch.setattr(sweep, "decrypt_json", lambda _: {})
    monkeypatch.setattr(sweep, client_name, lambda *args, **kwargs: client)
    monkeypatch.setattr(sweep, "_registrar_resposta_da_loja", AsyncMock())
    tracking = AsyncMock(
        side_effect=AssertionError("Other marketplaces must not query Amazon fallback")
    )
    monkeypatch.setattr(sweep, "load_amazon_correios_confirmations", tracking)
    result = await sweep._check_marketplace_shipped(None, integration, [pending_amazon, second])
    assert set(result) == {pending_amazon.bling_id}
    tracking.assert_not_called()


@pytest.mark.asyncio
async def test_mercadolivre_preserves_its_existing_shipment_path(pending_amazon, monkeypatch):
    from app.models import IntegrationPlatform

    integration = SimpleNamespace(platform=IntegrationPlatform.ML, credentials=b"test")
    client = object()
    monkeypatch.setattr(sweep, "decrypt_json", lambda _: {})
    monkeypatch.setattr(sweep, "MercadoLivreClient", lambda *args, **kwargs: client)
    native = AsyncMock(return_value=(pending_amazon.bling_id, None))
    monkeypatch.setattr(sweep, "_ml_shipped_for", native)
    tracking = AsyncMock(side_effect=AssertionError("ML must not query Amazon fallback"))
    monkeypatch.setattr(sweep, "load_amazon_correios_confirmations", tracking)
    result = await sweep._check_marketplace_shipped(None, integration, [pending_amazon])
    assert result == {pending_amazon.bling_id: None}
    assert native.await_args.args[:2] == (client, pending_amazon)
    tracking.assert_not_called()
