"""listings_import service — auto-link by SKU + import job orchestration."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    Company,
    Integration,
    IntegrationPlatform,
    Listing,
    ListingStatus,
    Marketplace,
    Product,
    Store,
    StoreStatus,
    User,
    UserRole,
    UserStatus,
)
from app.security.cipher import encrypt_json
from app.services.listings_import import (
    run_auto_import_link,
    run_import_listings,
)


@pytest_asyncio.fixture
async def user(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:svc-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"svc-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _make_integration(db: AsyncSession, user: User) -> Integration:
    company = Company(razao_social="ACME", apelido="acme")
    db.add(company)
    await db.flush()
    store = Store(company_id=company.id, marketplace=Marketplace.SHOPEE, status=StoreStatus.ACTIVE)
    db.add(store)
    await db.flush()
    integ = Integration(
        user_id=user.id,
        store_id=store.id,
        platform=IntegrationPlatform.SHOPEE,
        name="acme",
        credentials=encrypt_json({"access_token": "x", "shop_id": 1, "expires_at": 9999999999}),
    )
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    return integ


# ------------------------------------------------------------------ auto-link


@pytest.mark.asyncio
async def test_auto_link_attaches_listing_by_sku(db: AsyncSession, user: User):
    integ = await _make_integration(db, user)
    p = Product(user_id=user.id, sku="SKU-1", name="Widget", stock=0, min_stock=0)
    db.add(p)
    await db.flush()

    matched = Listing(
        user_id=user.id,
        integration_id=integ.id,
        platform=IntegrationPlatform.SHOPEE,
        external_id="ext-1",
        sku="SKU-1",
        title="Match",
        status=ListingStatus.ACTIVE,
    )
    blank = Listing(
        user_id=user.id,
        integration_id=integ.id,
        platform=IntegrationPlatform.SHOPEE,
        external_id="ext-2",
        sku="   ",
        title="Blank SKU",
        status=ListingStatus.ACTIVE,
    )
    no_sku = Listing(
        user_id=user.id,
        integration_id=integ.id,
        platform=IntegrationPlatform.SHOPEE,
        external_id="ext-3",
        sku=None,
        title="No SKU",
        status=ListingStatus.ACTIVE,
    )
    no_match = Listing(
        user_id=user.id,
        integration_id=integ.id,
        platform=IntegrationPlatform.SHOPEE,
        external_id="ext-4",
        sku="UNKNOWN",
        title="No Match",
        status=ListingStatus.ACTIVE,
    )
    db.add_all([matched, blank, no_sku, no_match])
    await db.commit()

    report = await run_auto_import_link(db)
    assert report["linked"] == 1

    rows = (await db.execute(select(Listing).order_by(Listing.external_id))).scalars().all()
    by_ext = {r.external_id: r for r in rows}
    assert by_ext["ext-1"].product_id == p.id
    assert by_ext["ext-2"].product_id is None  # blank SKU ignored
    assert by_ext["ext-3"].product_id is None
    assert by_ext["ext-4"].product_id is None


@pytest.mark.asyncio
async def test_auto_link_links_across_users_in_crm_mode(db: AsyncSession, user: User):
    """CRM mode: integrations/products/listings are shared. Auto-link matches
    on SKU globally — a listing owned by user A links to a product owned by
    user B if the SKU matches."""
    integ = await _make_integration(db, user)
    other = User(
        open_id=f"email:o2-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"o2-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
    )
    db.add(other)
    await db.commit()
    await db.refresh(other)
    other_integ = await _make_integration(db, other)

    other_product = Product(user_id=other.id, sku="SHARED", name="x", stock=0, min_stock=0)
    db.add(other_product)
    await db.flush()

    db.add(
        Listing(
            user_id=user.id,
            integration_id=integ.id,
            platform=IntegrationPlatform.SHOPEE,
            external_id="ext-1",
            sku="SHARED",
            title="t",
            status=ListingStatus.ACTIVE,
        )
    )
    db.add(
        Listing(
            user_id=other.id,
            integration_id=other_integ.id,
            platform=IntegrationPlatform.SHOPEE,
            external_id="ext-2",
            sku="SHARED",
            title="t",
            status=ListingStatus.ACTIVE,
        )
    )
    await db.commit()

    await run_auto_import_link(db)

    rows = (await db.execute(select(Listing).order_by(Listing.external_id))).scalars().all()
    by_ext = {r.external_id: r for r in rows}
    # Both listings link to the shared product regardless of owner.
    assert by_ext["ext-1"].product_id == other_product.id
    assert by_ext["ext-2"].product_id == other_product.id


# -------------------------------------------------------------- import job


@pytest.mark.asyncio
async def test_run_import_listings_upserts_and_links(
    db: AsyncSession, user: User, monkeypatch
):
    integ = await _make_integration(db, user)
    p = Product(user_id=user.id, sku="SKU-1", name="Widget", stock=0, min_stock=0)
    db.add(p)
    await db.flush()

    job = BackgroundJob(
        type=BackgroundJobType.IMPORT_LISTINGS,
        status=BackgroundJobStatus.PENDING,
        created_by=user.id,
        payload={"integration_id": str(integ.id)},
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    async def _fake_listings(self, **kw):
        for item in [
            {
                "external_id": "1001",
                "sku": "SKU-1",
                "title": "Widget Listing",
                "description": None,
                "price": 999,
                "stock": 10,
                "status": "active",
                "category": "1",
                "thumbnail_url": "http://x",
                "raw": {"id": 1001},
            },
            {
                "external_id": "",
                "sku": "BAD",
                "title": "Skipped",
                "status": "active",
                "raw": {},
            },
        ]:
            yield item

    from app.services.marketplaces import shopee as shopee_mod

    monkeypatch.setattr(shopee_mod.ShopeeClient, "list_listings", _fake_listings)

    await run_import_listings(
        db,
        job_id=job.id,
        user_id=user.id,
        integration_id=integ.id,
    )

    await db.refresh(job)
    assert job.status == BackgroundJobStatus.SUCCEEDED
    assert job.result["created"] == 1
    assert job.result["skipped"] == 1
    assert job.result["linked"] == 1

    rows = (await db.execute(select(Listing))).scalars().all()
    assert len(rows) == 1
    assert rows[0].external_id == "1001"
    assert rows[0].product_id == p.id
    assert rows[0].price == 999

    # Re-running should update, not duplicate.
    job2 = BackgroundJob(
        type=BackgroundJobType.IMPORT_LISTINGS,
        status=BackgroundJobStatus.PENDING,
        created_by=user.id,
        payload={"integration_id": str(integ.id)},
    )
    db.add(job2)
    await db.commit()
    await db.refresh(job2)
    await run_import_listings(
        db, job_id=job2.id, user_id=user.id, integration_id=integ.id
    )
    rows = (await db.execute(select(Listing))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_run_import_listings_unsupported_platform(
    db: AsyncSession, user: User
):
    company = Company(razao_social="ACME", apelido="acme")
    db.add(company)
    await db.flush()
    store = Store(company_id=company.id, marketplace=Marketplace.SHOPEE, status=StoreStatus.ACTIVE)
    db.add(store)
    await db.flush()
    integ = Integration(
        user_id=user.id,
        store_id=store.id,
        platform=IntegrationPlatform.BLING,
        name="bling",
        credentials=encrypt_json({"access_token": "x"}),
    )
    db.add(integ)
    job = BackgroundJob(
        type=BackgroundJobType.IMPORT_LISTINGS,
        status=BackgroundJobStatus.PENDING,
        created_by=user.id,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    await db.refresh(integ)

    await run_import_listings(
        db, job_id=job.id, user_id=user.id, integration_id=integ.id
    )
    await db.refresh(job)
    assert job.status == BackgroundJobStatus.FAILED
    assert job.error == "platform_not_supported:bling"
