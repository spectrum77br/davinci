"""Pricing push + idempotency (Fase 9b)."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
import respx
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Company,
    Integration,
    IntegrationPlatform,
    Listing,
    ListingStatus,
    Marketplace,
    PricingAccount,
    PricingOverride,
    PricingPlatform,
    PricingProduct,
    PricingPushIdempotency,
    Store,
    StoreStatus,
    User,
    UserRole,
    UserStatus,
)
from app.security.cipher import encrypt_json
from app.services.marketplaces.ml import ML_API_BASE

PERM_FULL = {
    "tabela_precos": {"view": True, "edit": True, "delete": True},
    "tabela_precos_contas": {"view": True, "edit": True, "delete": True},
    "tabela_precos_produtos": {"view": True, "edit": True, "delete": True},
}


def _ml_creds() -> dict[str, Any]:
    return {
        "client_id": "x",
        "client_secret": "y",
        "access_token": "tok",
        "refresh_token": "ref",
        "user_id": 1234,
        "expires_at": int(time.time()) + 3600,
    }


@pytest_asyncio.fixture
async def user_full(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:push-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"push-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions=PERM_FULL,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def ml_setup(db: AsyncSession, user_full: User) -> dict[str, Any]:
    """Build a complete ML push scenario: company/store/integration + listing
    + pricing_account/product wired so a push has all dependencies satisfied.
    """
    company = Company(razao_social="ACME", apelido="acme")
    db.add(company)
    await db.flush()
    store = Store(
        company_id=company.id,
        marketplace=Marketplace.ML,
        status=StoreStatus.ACTIVE,
    )
    db.add(store)
    await db.flush()
    integ = Integration(
        user_id=user_full.id,
        store_id=store.id,
        platform=IntegrationPlatform.ML,
        name="acme-ml",
        credentials=encrypt_json(_ml_creds()),
    )
    db.add(integ)
    await db.flush()

    listing = Listing(
        user_id=user_full.id,
        integration_id=integ.id,
        platform=IntegrationPlatform.ML,
        external_id="MLB1",
        sku="SKU-A",
        title="iPhone 15",
        status=ListingStatus.ACTIVE,
        price=10000,
        stock=5,
    )
    db.add(listing)

    account = PricingAccount(
        user_id=user_full.id,
        name="ml-acct",
        platform=PricingPlatform.ML,
        kit_number=1,
        commission=Decimal("0.10"),
        margin1=Decimal("0.20"),
        shipping1=Decimal("5.00"),
        integration_id=integ.id,
    )
    product = PricingProduct(
        user_id=user_full.id,
        sku="SKU-A",
        name="iPhone 15",
        cost_kit1=Decimal("100.00"),
    )
    db.add(account)
    db.add(product)
    await db.commit()
    await db.refresh(account)
    await db.refresh(product)
    return {"account": account, "product": product, "integration": integ, "listing": listing}


@pytest.mark.asyncio
async def test_push_single_cell_success(
    db: AsyncSession,
    client: AsyncClient,
    user_full: User,
    ml_setup: dict[str, Any],
    auth_as: Callable[[User | None], None],
):
    auth_as(user_full)
    with respx.mock(base_url=ML_API_BASE) as router:
        put_route = router.put("/items/MLB1").mock(
            return_value=Response(200, json={"id": "MLB1", "price": 138.89})
        )
        r = await client.post(
            "/api/pricing/push",
            headers={"Idempotency-Key": "test-key-1"},
            json={
                "items": [
                    {
                        "pricing_account_id": str(ml_setup["account"].id),
                        "pricing_product_id": str(ml_setup["product"].id),
                    }
                ]
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["results"]) == 1
        item = body["results"][0]
        assert item["ok"] is True
        assert item["code"] == "ok"
        assert item["item_id"] == "MLB1"
        assert Decimal(item["price"]) == Decimal("138.89")
        assert item["cached"] is False
        assert put_route.called
        # Verify body sent
        sent = put_route.calls[-1].request
        assert b'"price":138.89' in sent.content


@pytest.mark.asyncio
async def test_push_idempotent_replay_returns_cached(
    db: AsyncSession,
    client: AsyncClient,
    user_full: User,
    ml_setup: dict[str, Any],
    auth_as: Callable[[User | None], None],
):
    """B13 — duplicate POST with same Idempotency-Key returns cached
    response without re-hitting the marketplace.
    """
    auth_as(user_full)
    payload = {
        "items": [
            {
                "pricing_account_id": str(ml_setup["account"].id),
                "pricing_product_id": str(ml_setup["product"].id),
            }
        ]
    }
    with respx.mock(base_url=ML_API_BASE) as router:
        put_route = router.put("/items/MLB1").mock(
            return_value=Response(200, json={"id": "MLB1"})
        )
        h = {"Idempotency-Key": "replay-1"}
        r1 = await client.post("/api/pricing/push", headers=h, json=payload)
        r2 = await client.post("/api/pricing/push", headers=h, json=payload)
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert put_route.call_count == 1
        first = r1.json()["results"][0]
        second = r2.json()["results"][0]
        assert first["cached"] is False
        assert second["cached"] is True
        assert second["price"] == first["price"]
        assert second["code"] == first["code"]

    # Verify a row in the idempotency table exists
    rows = (
        await db.execute(select(PricingPushIdempotency))
    ).scalars().all()
    assert any(r.key == "replay-1" for r in rows)


@pytest.mark.asyncio
async def test_push_listing_not_found(
    db: AsyncSession,
    client: AsyncClient,
    user_full: User,
    ml_setup: dict[str, Any],
    auth_as: Callable[[User | None], None],
):
    auth_as(user_full)
    # Mutate product SKU so no listing matches
    ml_setup["product"].sku = "MISSING-SKU"
    await db.commit()
    r = await client.post(
        "/api/pricing/push",
        json={
            "items": [
                {
                    "pricing_account_id": str(ml_setup["account"].id),
                    "pricing_product_id": str(ml_setup["product"].id),
                }
            ]
        },
    )
    assert r.status_code == 200
    item = r.json()["results"][0]
    assert item["ok"] is False
    assert item["code"] == "listing_not_found"


@pytest.mark.asyncio
async def test_push_locked_cell_skipped(
    db: AsyncSession,
    client: AsyncClient,
    user_full: User,
    ml_setup: dict[str, Any],
    auth_as: Callable[[User | None], None],
):
    auth_as(user_full)
    db.add(
        PricingOverride(
            user_id=user_full.id,
            pricing_product_id=ml_setup["product"].id,
            pricing_account_id=ml_setup["account"].id,
            cell_status="locked",
            price_override=None,
        )
    )
    await db.commit()
    r = await client.post(
        "/api/pricing/push",
        json={
            "items": [
                {
                    "pricing_account_id": str(ml_setup["account"].id),
                    "pricing_product_id": str(ml_setup["product"].id),
                }
            ]
        },
    )
    item = r.json()["results"][0]
    assert item["ok"] is False
    assert item["code"] == "cell_locked"


@pytest.mark.asyncio
async def test_push_account_without_integration_returns_account_not_linked(
    db: AsyncSession,
    client: AsyncClient,
    user_full: User,
    ml_setup: dict[str, Any],
    auth_as: Callable[[User | None], None],
):
    auth_as(user_full)
    ml_setup["account"].integration_id = None
    await db.commit()
    r = await client.post(
        "/api/pricing/push",
        json={
            "items": [
                {
                    "pricing_account_id": str(ml_setup["account"].id),
                    "pricing_product_id": str(ml_setup["product"].id),
                }
            ]
        },
    )
    item = r.json()["results"][0]
    assert item["ok"] is False
    assert item["code"] == "account_not_linked"


@pytest.mark.asyncio
async def test_push_non_ml_platform_not_implemented(
    db: AsyncSession,
    client: AsyncClient,
    user_full: User,
    ml_setup: dict[str, Any],
    auth_as: Callable[[User | None], None],
):
    auth_as(user_full)
    # Re-platform integration to Shopee → push must refuse cleanly
    ml_setup["integration"].platform = IntegrationPlatform.SHOPEE
    await db.commit()
    r = await client.post(
        "/api/pricing/push",
        json={
            "items": [
                {
                    "pricing_account_id": str(ml_setup["account"].id),
                    "pricing_product_id": str(ml_setup["product"].id),
                }
            ]
        },
    )
    item = r.json()["results"][0]
    assert item["ok"] is False
    assert item["code"] == "platform_not_implemented"


@pytest.mark.asyncio
async def test_grid_returns_computed_cells(
    db: AsyncSession,
    client: AsyncClient,
    user_full: User,
    ml_setup: dict[str, Any],
    auth_as: Callable[[User | None], None],
):
    auth_as(user_full)
    r = await client.get("/api/pricing/grid")
    assert r.status_code == 200
    body = r.json()
    assert len(body["accounts"]) == 1
    assert len(body["products"]) == 1
    assert len(body["cells"]) == 1
    cell = body["cells"][0]
    assert cell["source"] == "computed"
    assert Decimal(cell["price"]) == Decimal("138.89")
