"""Carrefour price acceptance stays pending until the client confirms application.

These tests exercise the push flow without a database or remote marketplace.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models import CellStatus, IntegrationPlatform
from app.services.marketplaces.base import SyncResult, SyncStatus
from app.services.pricing import push

pytestmark = pytest.mark.asyncio


def _pending():
    return SyncResult(
        status=SyncStatus.REQUIRES_REVIEW,
        error_code="carrefour_write_pending",
        error_detail="Envio recebido; aguardando confirmação do Carrefour.",
        payload={"import_id": "1234"},
    )


def _link():
    return SimpleNamespace(
        id=uuid4(), product_id=uuid4(), external_id="123456", variation_id=None,
        external_sku="POOFY-001",
    )


def _scalar(value):
    return SimpleNamespace(scalar_one_or_none=lambda: value)


def _setup(monkeypatch, results, *, platform=IntegrationPlatform.CARREFOUR):
    integration = SimpleNamespace(
        id=uuid4(), platform=platform, archived_at=None, credentials="test-only",
    )
    account = SimpleNamespace(
        id=uuid4(), canal="kit", integration_id=integration.id, listing_type=None,
    )
    product = SimpleNamespace(id=uuid4(), sku="POOFY-001")
    user = SimpleNamespace(id=uuid4())
    override = SimpleNamespace(cell_status=CellStatus.AUTO)
    links = [_link() for _ in results]
    for index, link in enumerate(links):
        link.external_id = str(123456 + index)
    session = SimpleNamespace(execute=AsyncMock(side_effect=[
        _scalar(account), _scalar(product), _scalar(override), _scalar(integration),
        SimpleNamespace(all=lambda: [(link.product_id, "POOFY-001") for link in links]),
    ]))
    client = SimpleNamespace(update_price=AsyncMock(side_effect=results))
    persist = AsyncMock()
    store = AsyncMock()
    monkeypatch.setattr(push, "user_scope", lambda model, user: True)
    monkeypatch.setattr(push, "_resolve_product_type", AsyncMock(return_value=1))
    monkeypatch.setattr(push, "_account_department", AsyncMock(return_value=None))
    monkeypatch.setattr(push, "resolver_para_envio", AsyncMock(return_value=SimpleNamespace(
        links=links, bloqueados=[], so_catalogo=[],
    )))
    monkeypatch.setattr(push, "calculate", lambda *a, **kw: SimpleNamespace(
        source="calculated", price=Decimal("49.90"), inputs={"cost": "30"},
    ))
    monkeypatch.setattr(push, "decrypt_json", lambda value: {"shop_id": "64421"})
    monkeypatch.setattr(push, "client_for", lambda *a, **kw: client)
    monkeypatch.setattr(push, "_persist_cell_status", persist)
    monkeypatch.setattr(push, "_lookup_idempotent", AsyncMock(return_value=None))
    monkeypatch.setattr(push, "_store_idempotent", store)
    return SimpleNamespace(
        session=session, user=user, account=account, product=product, override=override,
        client=client, persist=persist, store=store, links=links,
    )


async def _run(setup, *, key=None):
    return await push.push_one(
        setup.session, user=setup.user, account_id=setup.account.id,
        product_id=setup.product.id, idempotency_key=key,
    )


async def test_carrefour_dispatch_passes_bound_link_and_price():
    link = _link()
    result = _pending()
    client = SimpleNamespace(update_price=AsyncMock(return_value=result))
    actual = await push._dispatch_price_update_link(
        client, IntegrationPlatform.CARREFOUR, link, 49.90,
        product_sku="LOCAL-SKU-MUST-NOT-REPLACE-THE-LINK",
    )
    assert actual is result
    client.update_price.assert_awaited_once_with(link=link, price=49.90)


async def test_carrefour_pending_is_not_retried_or_marked_applied(monkeypatch):
    setup = _setup(monkeypatch, [_pending()])
    response = await _run(setup, key="carrefour-pending")
    assert response.ok is False
    assert response.code == "carrefour_write_pending"
    assert "aguardando confirmação" in response.detail
    assert response.payload["pending"] is True
    assert response.payload["pending_count"] == 1
    assert response.payload["links"][0]["pending"] is True
    assert response.payload["links"][0]["success"] is False
    assert response.payload["links"][0]["status"] == SyncStatus.REQUIRES_REVIEW.value
    setup.persist.assert_not_awaited()
    assert setup.override.cell_status == CellStatus.AUTO
    setup.client.update_price.assert_awaited_once_with(link=setup.links[0], price=49.90)
    assert setup.store.await_args.args[-1]["code"] == "carrefour_write_pending"


async def test_carrefour_mixed_batch_stays_pending_without_cell_transition(monkeypatch):
    setup = _setup(monkeypatch, [
        SyncResult(status=SyncStatus.OK), _pending(),
        SyncResult(status=SyncStatus.FATAL, error_code="invalid_offer"),
    ])
    response = await _run(setup)
    assert response.ok is False
    assert response.code == "carrefour_write_pending"
    assert response.payload["pending_count"] == 1
    assert [r["success"] for r in response.payload["links"]] == [True, False, False]
    assert "1 confirmado(s)" in response.detail
    assert "1 falha(s): invalid_offer" in response.detail
    setup.persist.assert_not_awaited()
    assert setup.client.update_price.await_count == 3


@pytest.mark.parametrize("platform,result,expected_code,expected_status", [
    (IntegrationPlatform.CARREFOUR, SyncResult(status=SyncStatus.OK), "ok", CellStatus.AUTO),
    (
        IntegrationPlatform.CARREFOUR,
        SyncResult(status=SyncStatus.FATAL, error_code="invalid_offer"),
        "invalid_offer", CellStatus.ERROR,
    ),
    (IntegrationPlatform.AMAZON, _pending(), "carrefour_write_pending", CellStatus.ERROR),
    (
        IntegrationPlatform.CARREFOUR,
        SyncResult(status=SyncStatus.REQUIRES_REVIEW, error_code="offer_review"),
        "offer_review", CellStatus.ERROR,
    ),
])
async def test_confirmed_failures_and_other_marketplaces_keep_existing_semantics(
    monkeypatch, platform, result, expected_code, expected_status,
):
    setup = _setup(monkeypatch, [result], platform=platform)
    response = await _run(setup)
    assert response.code == expected_code
    assert response.ok is (result.status == SyncStatus.OK)
    assert "pending" not in response.payload
    setup.persist.assert_awaited_once()
    assert setup.persist.await_args.kwargs["new_status"] == expected_status


async def test_idempotent_pending_replay_does_not_submit_again(monkeypatch):
    setup = _setup(monkeypatch, [_pending()])
    first = await _run(setup, key="same-operation")
    monkeypatch.setattr(push, "_lookup_idempotent", AsyncMock(return_value=first.to_dict()))
    setup.session.execute = AsyncMock(side_effect=[
        _scalar(setup.account), _scalar(setup.product), _scalar(setup.override),
    ])
    replay = await _run(setup, key="same-operation")
    assert replay.cached is True
    assert replay.ok is False
    assert replay.code == "carrefour_write_pending"
    assert replay.payload["pending"] is True
    setup.client.update_price.assert_awaited_once()
    setup.persist.assert_not_awaited()
    setup.store.assert_awaited_once()
