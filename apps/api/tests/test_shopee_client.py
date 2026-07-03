"""Shopee client tests (Fase 4b.Shopee).

Covers B5: Shopee returns HTTP 200 with `error` JSON for app-level failures
(banned listings, auth errors, validation, server errors). The client must
read the body and route each into the correct LinkSyncStatus + error_code.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx
import pytest
import pytest_asyncio
import respx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Company,
    Integration,
    IntegrationPlatform,
    LinkSyncStatus,
    Marketplace,
    Product,
    ProductLink,
    Store,
    StoreStatus,
    User,
    UserRole,
    UserStatus,
)
from app.security.cipher import encrypt_json
from app.services.marketplaces.base import SyncStatus
from app.services.marketplaces.shopee import ShopeeClient


def _shopee_creds() -> dict[str, Any]:
    return {
        "shop_id": 99,
        "access_token": "tok",
        "refresh_token": "ref",
        "expires_at": int(time.time()) + 3600,
    }


@pytest_asyncio.fixture
async def user(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:sp-{uuid.uuid4().hex[:8]}@davinci-test.com",
        email=f"sp-{uuid.uuid4().hex[:8]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions={},
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _setup(db: AsyncSession, user: User) -> tuple[Integration, Product, ProductLink]:
    company = Company(
        razao_social="Shopee Co",
        cnpj=uuid.uuid4().hex[:14],
        apelido=f"sp-{uuid.uuid4().hex[:6]}",
        responsavel_id=user.id,
    )
    db.add(company)
    await db.flush()
    store = Store(
        company_id=company.id,
        marketplace=Marketplace.SHOPEE,
        status=StoreStatus.ACTIVE,
    )
    db.add(store)
    await db.flush()
    integ = Integration(
        user_id=user.id,
        store_id=store.id,
        platform=IntegrationPlatform.SHOPEE,
        name="shopee-test",
        credentials=encrypt_json(_shopee_creds()),
    )
    db.add(integ)
    await db.flush()
    product = Product(
        user_id=user.id,
        sku=f"sku-{uuid.uuid4().hex[:6]}",
        name="shopee widget",
        stock=10,
    )
    db.add(product)
    await db.flush()
    link = ProductLink(
        user_id=user.id,
        product_id=product.id,
        integration_id=integ.id,
        store_id=store.id,
        platform=IntegrationPlatform.SHOPEE,
        external_id="500",
        stock=5,
        last_sync_status=LinkSyncStatus.PENDING,
    )
    db.add(link)
    await db.commit()
    await db.refresh(link)
    await db.refresh(product)
    return integ, product, link


# ---------------------------------------------------------------- happy path


@pytest.mark.asyncio
async def test_update_stock_ok(db: AsyncSession, user: User) -> None:
    _, _, link = await _setup(db, user)
    client = ShopeeClient(_shopee_creds())
    with respx.mock(base_url=client._base) as router:
        route = router.post("/api/v2/product/update_stock").mock(
            return_value=httpx.Response(200, json={"error": "", "request_id": "req-1"})
        )
        result = await client.update_stock(link, 12)

    assert result.status == SyncStatus.OK
    assert result.qty_after == 12
    assert route.called
    body = route.calls.last.request.content
    assert b'"item_id": 500' in body or b'"item_id":500' in body
    assert b'"seller_stock"' in body
    assert b'"stock": 12' in body or b'"stock":12' in body


# ---------------------------------------------------------------- B5 (banned)


@pytest.mark.asyncio
async def test_b5_banned_explicit_code_goes_to_review(
    db: AsyncSession, user: User
) -> None:
    _, _, link = await _setup(db, user)
    client = ShopeeClient(_shopee_creds())
    with respx.mock(base_url=client._base) as router:
        router.post("/api/v2/product/update_stock").mock(
            return_value=httpx.Response(
                200,
                json={
                    "error": "error_item_banned",
                    "message": "item was banned by shopee",
                    "request_id": "req-2",
                },
            )
        )
        result = await client.update_stock(link, 5)

    assert result.status == SyncStatus.REQUIRES_REVIEW
    assert result.error_code == "error_item_banned"
    assert result.payload.get("shopee_classification") == "banned"


@pytest.mark.asyncio
async def test_b5_banned_via_message_keyword(db: AsyncSession, user: User) -> None:
    """Even when the error_code is unfamiliar, a 'ban' substring in message
    is enough to route the link to REQUIRES_REVIEW (still safer than FATAL)."""
    _, _, link = await _setup(db, user)
    client = ShopeeClient(_shopee_creds())
    with respx.mock(base_url=client._base) as router:
        router.post("/api/v2/product/update_stock").mock(
            return_value=httpx.Response(
                200,
                json={
                    "error": "error_zzz_unknown_op",
                    "message": "this product was banned for IP infringement",
                },
            )
        )
        result = await client.update_stock(link, 5)

    assert result.status == SyncStatus.REQUIRES_REVIEW
    assert result.payload.get("shopee_classification") == "banned"


# ---------------------------------------------------------------- B5 (auth vs server)


@pytest.mark.asyncio
async def test_b5_auth_error_goes_to_fatal_not_banned(
    db: AsyncSession, user: User
) -> None:
    """error_auth must NOT be routed to the banned bucket. It is FATAL — the
    integration needs reconnection, not a "banned product" alert."""
    _, _, link = await _setup(db, user)
    client = ShopeeClient(_shopee_creds())
    with respx.mock(base_url=client._base) as router:
        router.post("/api/v2/product/update_stock").mock(
            return_value=httpx.Response(
                200, json={"error": "error_auth", "message": "invalid access_token"}
            )
        )
        result = await client.update_stock(link, 5)

    assert result.status == SyncStatus.FATAL
    assert result.error_code == "error_auth"
    assert result.payload.get("shopee_classification") == "auth"


@pytest.mark.asyncio
async def test_b5_server_error_goes_to_retryable(db: AsyncSession, user: User) -> None:
    _, _, link = await _setup(db, user)
    client = ShopeeClient(_shopee_creds())
    with respx.mock(base_url=client._base) as router:
        router.post("/api/v2/product/update_stock").mock(
            return_value=httpx.Response(
                200,
                json={"error": "error_server", "message": "transient backend issue"},
            )
        )
        result = await client.update_stock(link, 5)

    assert result.status == SyncStatus.RETRYABLE
    assert result.error_code == "error_server"


@pytest.mark.asyncio
async def test_b5_param_error_goes_to_fatal(db: AsyncSession, user: User) -> None:
    _, _, link = await _setup(db, user)
    client = ShopeeClient(_shopee_creds())
    with respx.mock(base_url=client._base) as router:
        router.post("/api/v2/product/update_stock").mock(
            return_value=httpx.Response(
                200, json={"error": "error_param", "message": "missing item_id"}
            )
        )
        result = await client.update_stock(link, 5)

    assert result.status == SyncStatus.FATAL
    assert result.error_code == "error_param"


@pytest.mark.asyncio
async def test_b5_unknown_code_is_retryable_not_fatal(
    db: AsyncSession, user: User
) -> None:
    """Unrecognised error code: route to RETRYABLE (conservative). Mapping
    everything unknown to FATAL would silently drop transient new-failure
    modes Shopee adds without docs."""
    _, _, link = await _setup(db, user)
    client = ShopeeClient(_shopee_creds())
    with respx.mock(base_url=client._base) as router:
        router.post("/api/v2/product/update_stock").mock(
            return_value=httpx.Response(
                200,
                json={"error": "error_xyz_brand_new", "message": "what is this"},
            )
        )
        result = await client.update_stock(link, 5)

    assert result.status == SyncStatus.RETRYABLE
    assert result.payload.get("shopee_classification") == "unknown"


# ---------------------------------------------------------------- HTTP transport


@pytest.mark.asyncio
async def test_http_429_is_retryable(db: AsyncSession, user: User) -> None:
    _, _, link = await _setup(db, user)
    client = ShopeeClient(_shopee_creds())
    with respx.mock(base_url=client._base) as router:
        router.post("/api/v2/product/update_stock").mock(
            return_value=httpx.Response(429, text="rate limit")
        )
        result = await client.update_stock(link, 5)
    assert result.status == SyncStatus.RETRYABLE
    assert result.error_code == "http_429"


@pytest.mark.asyncio
async def test_invalid_external_id_is_fatal(db: AsyncSession, user: User) -> None:
    _, _, link = await _setup(db, user)
    link.external_id = "not-a-number"
    db.add(link)
    await db.commit()

    client = ShopeeClient(_shopee_creds())
    # No HTTP call expected.
    with respx.mock(base_url=client._base, assert_all_called=False) as router:
        result = await client.update_stock(link, 5)
        assert len(router.calls) == 0
    assert result.status == SyncStatus.FATAL
    assert result.error_code == "invalid_external_id"


@pytest.mark.asyncio
async def test_uses_variation_id_as_model_id(db: AsyncSession, user: User) -> None:
    """When the link has a variation_id, it goes into model_id; without one,
    model_id=0 (single-SKU listing path)."""
    _, _, link = await _setup(db, user)
    link.variation_id = "777"
    db.add(link)
    await db.commit()

    client = ShopeeClient(_shopee_creds())
    with respx.mock(base_url=client._base) as router:
        route = router.post("/api/v2/product/update_stock").mock(
            return_value=httpx.Response(200, json={"error": ""})
        )
        result = await client.update_stock(link, 9)
    assert result.status == SyncStatus.OK
    body = route.calls.last.request.content
    assert b'"model_id": 777' in body or b'"model_id":777' in body


# --------------------------------------------- batch failure_list / success_list
# Shopee's update_stock is a batch endpoint: it returns HTTP 200 with an EMPTY
# top-level `error` even when the pushed model was rejected — the rejection is
# hidden in response.failure_list. Reading only the top-level `error` reported
# OK for a push that never landed, so the panel showed "enviado N" while Shopee
# kept the old stock (the dup-listing bug on conta mega, i215.sa). These tests
# pin the confirm-before-OK behavior.


@pytest.mark.asyncio
async def test_update_stock_model_in_failure_list_is_not_ok(
    db: AsyncSession, user: User
) -> None:
    """error='' but the pushed model is in failure_list → NOT OK, and the real
    failed_reason is surfaced. qty_after stays None so the orchestrator has
    nothing to mirror into link.stock."""
    _, _, link = await _setup(db, user)
    link.variation_id = "159554631961"
    db.add(link)
    await db.commit()

    client = ShopeeClient(_shopee_creds())
    with respx.mock(base_url=client._base) as router:
        router.post("/api/v2/product/update_stock").mock(
            return_value=httpx.Response(
                200,
                json={
                    "error": "",
                    "message": "",
                    "response": {
                        "failure_list": [
                            {
                                "model_id": 159554631961,
                                "failed_reason": "stock is locked by an ongoing promotion",
                            }
                        ],
                        "success_list": [],
                    },
                },
            )
        )
        result = await client.update_stock(link, 1)

    assert result.status == SyncStatus.RETRYABLE
    assert result.error_code == "shopee_stock_failed"
    assert "promotion" in (result.error_detail or "")
    assert result.qty_after is None


@pytest.mark.asyncio
async def test_update_stock_success_list_confirms_ok(
    db: AsyncSession, user: User
) -> None:
    """error='' and the pushed model is in success_list → OK with qty_after."""
    _, _, link = await _setup(db, user)
    link.variation_id = "239432288068"
    db.add(link)
    await db.commit()

    client = ShopeeClient(_shopee_creds())
    with respx.mock(base_url=client._base) as router:
        router.post("/api/v2/product/update_stock").mock(
            return_value=httpx.Response(
                200,
                json={
                    "error": "",
                    "response": {
                        "failure_list": [],
                        "success_list": [
                            {"model_id": 239432288068, "location_id": "", "stock": 7}
                        ],
                    },
                },
            )
        )
        result = await client.update_stock(link, 7)

    assert result.status == SyncStatus.OK
    assert result.qty_after == 7


@pytest.mark.asyncio
async def test_update_stock_model_absent_from_both_lists_not_confirmed(
    db: AsyncSession, user: User
) -> None:
    """error='' but our model is in neither list (Shopee silently dropped it) →
    RETRYABLE, never a mirrored OK."""
    _, _, link = await _setup(db, user)
    link.variation_id = "111111111111"
    db.add(link)
    await db.commit()

    client = ShopeeClient(_shopee_creds())
    with respx.mock(base_url=client._base) as router:
        router.post("/api/v2/product/update_stock").mock(
            return_value=httpx.Response(
                200,
                json={
                    "error": "",
                    "response": {
                        "failure_list": [],
                        "success_list": [
                            {"model_id": 999999999999, "location_id": "", "stock": 3}
                        ],
                    },
                },
            )
        )
        result = await client.update_stock(link, 3)

    assert result.status == SyncStatus.RETRYABLE
    assert result.error_code == "shopee_stock_not_confirmed"
    assert result.qty_after is None


@pytest.mark.asyncio
async def test_update_stock_failure_list_abnormal_goes_to_review(
    db: AsyncSession, user: User
) -> None:
    """A dead-listing reason (deleted/abnormal) routes to REQUIRES_REVIEW so a
    human unlinks the anúncio instead of retrying it forever (a055.sa /
    SELLER_DELETE case, but reported via failure_list without a top error)."""
    _, _, link = await _setup(db, user)
    link.variation_id = "179410478625"
    db.add(link)
    await db.commit()

    client = ShopeeClient(_shopee_creds())
    with respx.mock(base_url=client._base) as router:
        router.post("/api/v2/product/update_stock").mock(
            return_value=httpx.Response(
                200,
                json={
                    "error": "",
                    "response": {
                        "failure_list": [
                            {
                                "model_id": 179410478625,
                                "failed_reason": "All the fields cannot be updated because the product status is abnormal",
                            }
                        ],
                        "success_list": [],
                    },
                },
            )
        )
        result = await client.update_stock(link, 67)

    assert result.status == SyncStatus.REQUIRES_REVIEW
    assert result.error_code == "shopee_stock_rejected"
    assert result.payload.get("shopee_classification") == "banned"


@pytest.mark.asyncio
async def test_update_stock_no_response_body_stays_ok(
    db: AsyncSession, user: User
) -> None:
    """Regression guard: a bare `{"error": ""}` (no batch lists — the shape the
    older mocks and some Shopee responses use) must still be OK."""
    _, _, link = await _setup(db, user)
    client = ShopeeClient(_shopee_creds())
    with respx.mock(base_url=client._base) as router:
        router.post("/api/v2/product/update_stock").mock(
            return_value=httpx.Response(200, json={"error": "", "request_id": "r"})
        )
        result = await client.update_stock(link, 5)
    assert result.status == SyncStatus.OK
    assert result.qty_after == 5
