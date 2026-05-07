"""SKU audit + competitor + store_info + auto-match + cost sync (Fase 9d)."""

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
    AuditDismissedSku,
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    Company,
    Integration,
    IntegrationPlatform,
    Listing,
    ListingStatus,
    Marketplace,
    PricingAccount,
    PricingPlatform,
    PricingProduct,
    Product,
    Store,
    StoreInfo,
    StoreStatus,
    User,
    UserRole,
    UserStatus,
)
from app.security.cipher import decrypt, encrypt_json
from app.services.marketplaces.bling import BLING_API_BASE
from app.services.pricing import competitor as comp_mod
from app.services.pricing.cost_sync import run_sync_bling_costs

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


def _bling_creds() -> dict[str, Any]:
    return {
        "client_id": "bx",
        "client_secret": "by",
        "access_token": "btok",
        "refresh_token": "bref",
        "expires_at": int(time.time()) + 3600,
    }


@pytest_asyncio.fixture
async def user_full(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:d-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"d-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions=PERM_FULL,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


# =================================================== sku-audit


@pytest_asyncio.fixture
async def audit_setup(db: AsyncSession, user_full: User) -> dict[str, Any]:
    company = Company(razao_social="ACME", apelido="acme")
    db.add(company)
    await db.flush()
    store = Store(
        company_id=company.id, marketplace=Marketplace.ML, status=StoreStatus.ACTIVE
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

    db.add_all(
        [
            Listing(
                user_id=user_full.id,
                integration_id=integ.id,
                platform=IntegrationPlatform.ML,
                external_id="MLB-A1",
                sku="HAS-PP",
                title="x1",
                status=ListingStatus.ACTIVE,
            ),
            Listing(
                user_id=user_full.id,
                integration_id=integ.id,
                platform=IntegrationPlatform.ML,
                external_id="MLB-A2",
                sku="MISSING-1",
                title="missing one",
                status=ListingStatus.ACTIVE,
            ),
            Listing(
                user_id=user_full.id,
                integration_id=integ.id,
                platform=IntegrationPlatform.ML,
                external_id="MLB-A3",
                sku="MISSING-1",
                title="missing two",
                status=ListingStatus.ACTIVE,
            ),
            Listing(
                user_id=user_full.id,
                integration_id=integ.id,
                platform=IntegrationPlatform.ML,
                external_id="MLB-A4",
                sku="MISSING-2",
                title="missing alone",
                status=ListingStatus.ACTIVE,
            ),
        ]
    )
    db.add(
        PricingProduct(
            user_id=user_full.id,
            sku="HAS-PP",
            name="present",
            cost_kit1=Decimal("1.00"),
        )
    )
    await db.commit()
    return {"integration": integ}


@pytest.mark.asyncio
async def test_sku_audit_lists_skus_missing_in_pricing_products(
    client: AsyncClient,
    user_full: User,
    audit_setup: dict[str, Any],
    auth_as: Callable[[User | None], None],
):
    auth_as(user_full)
    r = await client.get("/api/pricing/sku-audit")
    body = r.json()
    skus = {row["sku"]: row for row in body}
    # HAS-PP shouldn't appear (already in pricing_products).
    assert "HAS-PP" not in skus
    assert skus["MISSING-1"]["listing_count"] == 2
    assert skus["MISSING-2"]["listing_count"] == 1
    # Sorted by count desc → MISSING-1 first.
    assert body[0]["sku"] == "MISSING-1"


@pytest.mark.asyncio
async def test_sku_audit_dismiss_and_undismiss_round_trip(
    db: AsyncSession,
    client: AsyncClient,
    user_full: User,
    audit_setup: dict[str, Any],
    auth_as: Callable[[User | None], None],
):
    auth_as(user_full)
    r = await client.post("/api/pricing/sku-audit/MISSING-1/dismiss")
    assert r.status_code == 204
    rows = (
        await db.execute(
            select(AuditDismissedSku).where(AuditDismissedSku.user_id == user_full.id)
        )
    ).scalars().all()
    assert {r.sku for r in rows} == {"MISSING-1"}

    # Default scan filters out dismissed.
    r = await client.get("/api/pricing/sku-audit")
    skus = {row["sku"] for row in r.json()}
    assert "MISSING-1" not in skus

    # include_dismissed=true brings it back, flagged.
    r = await client.get("/api/pricing/sku-audit?include_dismissed=true")
    by = {row["sku"]: row for row in r.json()}
    assert by["MISSING-1"]["dismissed"] is True

    # Idempotent: second dismiss is a no-op.
    r = await client.post("/api/pricing/sku-audit/MISSING-1/dismiss")
    assert r.status_code == 204
    rows = (
        await db.execute(
            select(AuditDismissedSku).where(AuditDismissedSku.user_id == user_full.id)
        )
    ).scalars().all()
    assert len(rows) == 1

    r = await client.post("/api/pricing/sku-audit/MISSING-1/undismiss")
    assert r.status_code == 204
    r = await client.get("/api/pricing/sku-audit")
    assert any(row["sku"] == "MISSING-1" for row in r.json())


# =================================================== competitor


@pytest.mark.asyncio
async def test_competitor_search_calls_ml_public_and_caches(
    client: AsyncClient,
    user_full: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(user_full)
    comp_mod.cache_clear()
    payload = {
        "results": [
            {
                "id": "MLB1",
                "title": "iPhone 15 128GB",
                "price": 5999.0,
                "currency_id": "BRL",
                "permalink": "https://produto.mercadolivre.com.br/MLB1",
                "seller": {"id": 12345},
                "condition": "new",
                "sold_quantity": 50,
                "available_quantity": 3,
                "thumbnail": "https://cdn/t.jpg",
            }
        ]
    }
    with respx.mock(base_url="https://api.mercadolibre.com") as router:
        route = router.get("/sites/MLB/search").mock(
            return_value=Response(200, json=payload)
        )
        r = await client.get("/api/pricing/competitor-prices?q=iphone&limit=5")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["item_id"] == "MLB1"
        assert body[0]["price"] == 5999.0

        # Cache hit: second call must NOT hit ML.
        r2 = await client.get("/api/pricing/competitor-prices?q=iphone&limit=5")
        assert r2.json()[0]["item_id"] == "MLB1"
        assert route.call_count == 1


@pytest.mark.asyncio
async def test_competitor_handles_ml_error_gracefully(
    client: AsyncClient,
    user_full: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(user_full)
    comp_mod.cache_clear()
    with respx.mock(base_url="https://api.mercadolibre.com") as router:
        router.get("/sites/MLB/search").mock(return_value=Response(503))
        r = await client.get("/api/pricing/competitor-prices?q=temudown")
        assert r.status_code == 200
        assert r.json() == []


# =================================================== auto-match


@pytest.mark.asyncio
async def test_auto_match_links_account_to_only_integration_of_platform(
    db: AsyncSession,
    client: AsyncClient,
    user_full: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(user_full)
    company = Company(razao_social="ACME", apelido="acme")
    db.add(company)
    await db.flush()
    store = Store(company_id=company.id, marketplace=Marketplace.ML, status=StoreStatus.ACTIVE)
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
    db.add(
        PricingAccount(
            user_id=user_full.id,
            name="ml-acct",
            platform=PricingPlatform.ML,
        )
    )
    db.add(
        PricingAccount(
            user_id=user_full.id,
            name="shopee-acct",
            platform=PricingPlatform.SHOPEE,  # no shopee integration → skipped
        )
    )
    await db.commit()

    r = await client.post("/api/pricing/accounts/auto-match")
    assert r.status_code == 200
    body = r.json()
    assert body["matched"] == 1
    assert body["skipped"] == 1


@pytest.mark.asyncio
async def test_auto_match_picks_by_name_when_multiple_integrations(
    db: AsyncSession,
    client: AsyncClient,
    user_full: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(user_full)
    company = Company(razao_social="ACME", apelido="acme")
    db.add(company)
    await db.flush()
    store = Store(company_id=company.id, marketplace=Marketplace.ML, status=StoreStatus.ACTIVE)
    db.add(store)
    await db.flush()
    db.add(
        Integration(
            user_id=user_full.id,
            store_id=store.id,
            platform=IntegrationPlatform.ML,
            name="luno-ml",
            credentials=encrypt_json(_ml_creds()),
        )
    )
    store2 = Store(company_id=company.id, marketplace=Marketplace.SHOPEE, status=StoreStatus.ACTIVE)
    db.add(store2)
    await db.flush()
    target = Integration(
        user_id=user_full.id,
        store_id=store2.id,
        platform=IntegrationPlatform.ML,
        name="mini-ml",
        credentials=encrypt_json(_ml_creds()),
    )
    db.add(target)
    db.add(
        PricingAccount(
            user_id=user_full.id,
            name="mini conta",
            platform=PricingPlatform.ML,
        )
    )
    await db.commit()
    await db.refresh(target)

    r = await client.post("/api/pricing/accounts/auto-match")
    assert r.json()["matched"] == 1

    acc = (
        await db.execute(select(PricingAccount).where(PricingAccount.name == "mini conta"))
    ).scalar_one()
    assert acc.integration_id == target.id


# =================================================== set-department


@pytest.mark.asyncio
async def test_set_account_department(
    db: AsyncSession,
    client: AsyncClient,
    user_full: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(user_full)
    acc = PricingAccount(
        user_id=user_full.id,
        name="x",
        platform=PricingPlatform.ML,
    )
    db.add(acc)
    await db.commit()
    await db.refresh(acc)
    r = await client.post(
        f"/api/pricing/accounts/{acc.id}/department",
        json={"department": "catalogo"},
    )
    assert r.status_code == 200
    assert r.json()["department"] == "catalogo"

    r = await client.post(
        f"/api/pricing/accounts/{acc.id}/department",
        json={"department": "ufo"},
    )
    assert r.status_code == 400


# =================================================== store_info


@pytest.mark.asyncio
async def test_store_info_crud_with_password_encrypt(
    db: AsyncSession,
    client: AsyncClient,
    user_full: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(user_full)
    r = await client.post(
        "/api/pricing/store-info",
        json={
            "platform": "shopee",
            "account_name": "loja shopee",
            "password": "s3cr3t",
        },
    )
    assert r.status_code == 201
    sid = r.json()["id"]
    assert r.json()["has_password"] is True
    assert "password_enc" not in r.json()

    row = (
        await db.execute(select(StoreInfo).where(StoreInfo.id == sid))
    ).scalar_one()
    assert decrypt(row.password_enc) == "s3cr3t"

    # PATCH password to empty clears it
    r = await client.patch(f"/api/pricing/store-info/{sid}", json={"password": ""})
    assert r.status_code == 200
    await db.refresh(row)
    assert row.password_enc is None

    r = await client.delete(f"/api/pricing/store-info/{sid}")
    assert r.status_code == 204


# =================================================== bling cost sync


@pytest.mark.asyncio
async def test_sync_bling_costs_updates_pricing_product_cost(
    db: AsyncSession,
    user_full: User,
):
    company = Company(razao_social="ACME", apelido="acme")
    db.add(company)
    await db.flush()
    store = Store(
        company_id=company.id, marketplace=Marketplace.ML, status=StoreStatus.ACTIVE
    )
    db.add(store)
    await db.flush()
    integ = Integration(
        user_id=user_full.id,
        store_id=store.id,
        platform=IntegrationPlatform.BLING,
        name="acme-bling",
        credentials=encrypt_json(_bling_creds()),
    )
    db.add(integ)
    await db.flush()

    db.add(
        Product(
            user_id=user_full.id,
            sku="BCOST-1",
            name="cost item",
            bling_product_id=42,
            integration_id=integ.id,
        )
    )
    pp = PricingProduct(
        user_id=user_full.id,
        sku="BCOST-1",
        name="cost item",
        cost_kit1=Decimal("0"),
        bling_cost_price=Decimal("9.99"),
    )
    db.add(pp)

    job = BackgroundJob(
        type=BackgroundJobType.SYNC_BLING_COSTS,
        status=BackgroundJobStatus.PENDING,
        created_by=user_full.id,
        payload={},
    )
    db.add(job)
    await db.commit()
    await db.refresh(pp)
    await db.refresh(job)

    with respx.mock(base_url=BLING_API_BASE) as router:
        router.get("/produtos/42").mock(
            return_value=Response(
                200, json={"data": {"id": 42, "precoCusto": 123.45}}
            )
        )
        await run_sync_bling_costs(db, job_id=job.id, user_id=user_full.id)

    await db.refresh(pp)
    await db.refresh(job)
    assert pp.bling_cost_price == Decimal("123.45")
    assert job.status == BackgroundJobStatus.SUCCEEDED
    assert job.result["summary"]["updated"] == 1


@pytest.mark.asyncio
async def test_sync_bling_costs_no_integration_fails_clean(
    db: AsyncSession,
    user_full: User,
):
    job = BackgroundJob(
        type=BackgroundJobType.SYNC_BLING_COSTS,
        status=BackgroundJobStatus.PENDING,
        created_by=user_full.id,
        payload={},
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    await run_sync_bling_costs(db, job_id=job.id, user_id=user_full.id)
    await db.refresh(job)
    assert job.status == BackgroundJobStatus.FAILED
    assert job.error == "no_bling_integration"


@pytest.mark.asyncio
async def test_sync_bling_costs_endpoint_enqueues(
    db: AsyncSession,
    client: AsyncClient,
    user_full: User,
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    auth_as(user_full)

    enqueued: dict = {}

    class FakePool:
        async def enqueue_job(self, fn_name, *args, **kwargs):
            enqueued["fn"] = fn_name

            class _J:
                job_id = "arq-cost-1"

            return _J()

    async def fake_pool():
        return FakePool()

    import app.routers.pricing as pricing_mod

    monkeypatch.setattr(pricing_mod, "get_arq_pool", fake_pool)

    r = await client.post("/api/pricing/jobs/sync-bling-costs")
    assert r.status_code == 201
    assert enqueued["fn"] == "sync_bling_costs_run"

    rows = (
        await db.execute(
            select(BackgroundJob).where(
                BackgroundJob.type == BackgroundJobType.SYNC_BLING_COSTS
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].arq_job_id == "arq-cost-1"
