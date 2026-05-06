"""Amazon SP-API client tests (Fase 4b.Amazon)."""

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
from app.services.marketplaces.amazon import (
    SP_API_BASE_NA,
    AmazonClient,
)
from app.services.marketplaces.base import SyncStatus

BR_MARKETPLACE = "A2Q3Y263D00KWC"


def _amz_creds() -> dict[str, Any]:
    return {
        "lwa_app_id": "amzn1.app.x",
        "lwa_client_secret": "secret",
        "refresh_token": "Atzr|...",
        "seller_id": "ASELLER123",
        "marketplace_id": BR_MARKETPLACE,
        "access_token": "Atza|tok",
        "expires_at": int(time.time()) + 3600,
    }


@pytest_asyncio.fixture
async def user(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:az-{uuid.uuid4().hex[:8]}@davinci-test.com",
        email=f"az-{uuid.uuid4().hex[:8]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions={},
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _setup(
    db: AsyncSession, user: User, *, sku: str = "MY-SKU-001"
) -> tuple[Integration, Product, ProductLink]:
    company = Company(
        razao_social="Amazon Co",
        cnpj=uuid.uuid4().hex[:14],
        apelido=f"az-{uuid.uuid4().hex[:6]}",
        responsavel_id=user.id,
    )
    db.add(company)
    await db.flush()
    store = Store(
        company_id=company.id,
        marketplace=Marketplace.AMAZON,
        status=StoreStatus.ACTIVE,
    )
    db.add(store)
    await db.flush()
    integ = Integration(
        user_id=user.id,
        store_id=store.id,
        platform=IntegrationPlatform.AMAZON,
        name="amazon-test",
        credentials=encrypt_json(_amz_creds()),
    )
    db.add(integ)
    await db.flush()
    product = Product(
        user_id=user.id,
        sku=sku,
        name="amazon widget",
        stock=10,
    )
    db.add(product)
    await db.flush()
    link = ProductLink(
        user_id=user.id,
        product_id=product.id,
        integration_id=integ.id,
        store_id=store.id,
        platform=IntegrationPlatform.AMAZON,
        external_id="ASIN-IGNORED",
        external_sku=sku,
        stock=5,
        last_sync_status=LinkSyncStatus.PENDING,
    )
    db.add(link)
    await db.commit()
    await db.refresh(link)
    return integ, product, link


# ---------------------------------------------------------------- happy path


@pytest.mark.asyncio
async def test_update_stock_ok(db: AsyncSession, user: User) -> None:
    _, _, link = await _setup(db, user)
    client = AmazonClient(_amz_creds())

    with respx.mock(base_url=SP_API_BASE_NA) as router:
        route = router.patch(
            f"/listings/2021-08-01/items/ASELLER123/{link.external_sku}"
        ).mock(
            return_value=httpx.Response(
                200,
                json={"submissionId": "SUB-1", "status": "ACCEPTED", "issues": []},
            )
        )
        result = await client.update_stock(link, 12)

    assert result.status == SyncStatus.OK
    assert result.qty_after == 12
    assert route.called

    # Body must carry quantity 12 and the BR marketplace via query param.
    last = route.calls.last
    body = last.request.content
    assert b'"quantity": 12' in body or b'"quantity":12' in body
    assert BR_MARKETPLACE.encode() in last.request.url.query


@pytest.mark.asyncio
async def test_uses_external_id_when_external_sku_absent(
    db: AsyncSession, user: User
) -> None:
    _, _, link = await _setup(db, user)
    link.external_sku = None
    link.external_id = "FALLBACK-SKU"
    db.add(link)
    await db.commit()

    client = AmazonClient(_amz_creds())
    with respx.mock(base_url=SP_API_BASE_NA) as router:
        route = router.patch("/listings/2021-08-01/items/ASELLER123/FALLBACK-SKU").mock(
            return_value=httpx.Response(200, json={"status": "ACCEPTED"})
        )
        result = await client.update_stock(link, 7)
    assert result.status == SyncStatus.OK
    assert route.called


# ---------------------------------------------------------------- error buckets


@pytest.mark.asyncio
async def test_404_is_fatal_sku_not_found(db: AsyncSession, user: User) -> None:
    _, _, link = await _setup(db, user)
    client = AmazonClient(_amz_creds())
    with respx.mock(base_url=SP_API_BASE_NA) as router:
        router.patch(
            f"/listings/2021-08-01/items/ASELLER123/{link.external_sku}"
        ).mock(return_value=httpx.Response(404, json={"errors": [{"code": "NotFound"}]}))
        result = await client.update_stock(link, 5)
    assert result.status == SyncStatus.FATAL
    assert result.error_code == "amazon_sku_not_found"


@pytest.mark.asyncio
async def test_403_is_fatal_auth(db: AsyncSession, user: User) -> None:
    _, _, link = await _setup(db, user)
    client = AmazonClient(_amz_creds())
    with respx.mock(base_url=SP_API_BASE_NA) as router:
        router.patch(
            f"/listings/2021-08-01/items/ASELLER123/{link.external_sku}"
        ).mock(return_value=httpx.Response(403, json={"errors": [{"code": "Unauthorized"}]}))
        result = await client.update_stock(link, 5)
    assert result.status == SyncStatus.FATAL
    assert result.error_code == "amazon_auth_403"


@pytest.mark.asyncio
async def test_429_is_retryable(db: AsyncSession, user: User) -> None:
    _, _, link = await _setup(db, user)
    client = AmazonClient(_amz_creds())
    with respx.mock(base_url=SP_API_BASE_NA) as router:
        router.patch(
            f"/listings/2021-08-01/items/ASELLER123/{link.external_sku}"
        ).mock(return_value=httpx.Response(429, text="Throttled"))
        result = await client.update_stock(link, 5)
    assert result.status == SyncStatus.RETRYABLE
    assert result.error_code == "http_429"


@pytest.mark.asyncio
async def test_invalid_status_with_issues_is_fatal(
    db: AsyncSession, user: User
) -> None:
    """Listings API returns 200 with status=INVALID + issues[] for partial
    failures. Treat ERROR-severity issues as FATAL (caller must fix data)."""
    _, _, link = await _setup(db, user)
    client = AmazonClient(_amz_creds())
    with respx.mock(base_url=SP_API_BASE_NA) as router:
        router.patch(
            f"/listings/2021-08-01/items/ASELLER123/{link.external_sku}"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "INVALID",
                    "issues": [
                        {
                            "severity": "ERROR",
                            "code": "INVALID_VALUE",
                            "message": "fulfillment_availability missing required attribute",
                        }
                    ],
                },
            )
        )
        result = await client.update_stock(link, 5)
    assert result.status == SyncStatus.FATAL
    assert result.error_code == "amazon_invalid_patch"


@pytest.mark.asyncio
async def test_warning_only_issues_dont_block(db: AsyncSession, user: User) -> None:
    """If status=ACCEPTED but there are WARNING issues, the patch still went
    through; treat as OK."""
    _, _, link = await _setup(db, user)
    client = AmazonClient(_amz_creds())
    with respx.mock(base_url=SP_API_BASE_NA) as router:
        router.patch(
            f"/listings/2021-08-01/items/ASELLER123/{link.external_sku}"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "ACCEPTED",
                    "issues": [
                        {"severity": "WARNING", "code": "X", "message": "minor"}
                    ],
                },
            )
        )
        result = await client.update_stock(link, 5)
    assert result.status == SyncStatus.OK


@pytest.mark.asyncio
async def test_missing_seller_id_is_fatal(db: AsyncSession, user: User) -> None:
    _, _, link = await _setup(db, user)
    creds = _amz_creds()
    creds["seller_id"] = ""
    client = AmazonClient(creds)
    # No HTTP call expected.
    with respx.mock(base_url=SP_API_BASE_NA, assert_all_called=False) as router:
        result = await client.update_stock(link, 5)
        assert len(router.calls) == 0
    assert result.status == SyncStatus.FATAL
    assert result.error_code == "amazon_missing_creds"


@pytest.mark.asyncio
async def test_no_sku_is_fatal(db: AsyncSession, user: User) -> None:
    _, _, link = await _setup(db, user)
    link.external_sku = None
    link.external_id = ""
    db.add(link)
    await db.commit()

    client = AmazonClient(_amz_creds())
    with respx.mock(base_url=SP_API_BASE_NA, assert_all_called=False) as router:
        result = await client.update_stock(link, 5)
        assert len(router.calls) == 0
    assert result.status == SyncStatus.FATAL
    assert result.error_code == "invalid_sku"
