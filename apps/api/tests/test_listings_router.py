"""Listings + listing_requests router (Fase 8)."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
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

PERM_VIEW = {"anuncios": {"view": True, "edit": False, "delete": False}}
PERM_EDIT = {"anuncios": {"view": True, "edit": True, "delete": False}}
PERM_FULL = {"anuncios": {"view": True, "edit": True, "delete": True}}


@pytest_asyncio.fixture
async def user_full(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:lst-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"lst-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions=PERM_FULL,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def other_user(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:oth-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"oth-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions=PERM_FULL,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _make_integration(
    db: AsyncSession, user: User, *, platform=IntegrationPlatform.SHOPEE
) -> Integration:
    company = Company(razao_social="ACME", apelido="acme")
    db.add(company)
    await db.flush()
    store = Store(company_id=company.id, marketplace=Marketplace.SHOPEE, status=StoreStatus.ACTIVE)
    db.add(store)
    await db.flush()
    integ = Integration(
        user_id=user.id,
        store_id=store.id,
        platform=platform,
        name="acme-shopee",
        credentials=encrypt_json({"access_token": "x"}),
    )
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    return integ


def _listing(
    user_id,
    integration_id,
    *,
    sku=None,
    external_id=None,
    title="t",
    status=ListingStatus.ACTIVE,
) -> Listing:
    return Listing(
        user_id=user_id,
        integration_id=integration_id,
        platform=IntegrationPlatform.SHOPEE,
        external_id=external_id or uuid.uuid4().hex[:8],
        sku=sku,
        title=title,
        status=status,
    )


# ------------------------------------------------------------------- list/get


@pytest.mark.asyncio
async def test_list_listings_user_scope_only(
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
    db: AsyncSession,
    user_full: User,
    other_user: User,
):
    integ = await _make_integration(db, user_full)
    other_integ = await _make_integration(db, other_user)
    db.add(_listing(user_full.id, integ.id, title="mine"))
    db.add(_listing(other_user.id, other_integ.id, title="not-mine"))
    await db.commit()

    auth_as(user_full)
    r = await client.get("/api/listings")
    assert r.status_code == 200, r.text
    body = r.json()
    titles = [i["title"] for i in body["items"]]
    assert "not-mine" not in titles
    assert titles == ["mine"]
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_list_listings_filters(
    client, auth_as, db, user_full
):
    integ = await _make_integration(db, user_full)
    db.add(_listing(user_full.id, integ.id, sku="ABC", title="alpha"))
    db.add(_listing(user_full.id, integ.id, sku="XYZ", title="beta", status=ListingStatus.PAUSED))
    db.add(_listing(user_full.id, integ.id, sku=None, title="gamma"))
    await db.commit()

    auth_as(user_full)
    r = await client.get("/api/listings?status=paused")
    assert [i["title"] for i in r.json()["items"]] == ["beta"]

    r = await client.get("/api/listings?search=alp")
    assert [i["title"] for i in r.json()["items"]] == ["alpha"]

    r = await client.get("/api/listings?unlinked=true")
    titles = {i["title"] for i in r.json()["items"]}
    assert titles == {"alpha", "beta", "gamma"}


@pytest.mark.asyncio
async def test_get_listing_404_other_user(
    client, auth_as, db, user_full, other_user
):
    other_integ = await _make_integration(db, other_user)
    row = _listing(other_user.id, other_integ.id, title="t")
    db.add(row)
    await db.commit()
    await db.refresh(row)

    auth_as(user_full)
    r = await client.get(f"/api/listings/{row.id}")
    assert r.status_code == 404


# ------------------------------------------------------------------- patch/delete


@pytest.mark.asyncio
async def test_patch_listing_links_product(
    client, auth_as, db, user_full
):
    integ = await _make_integration(db, user_full)
    p = Product(user_id=user_full.id, sku="SKU-1", name="Widget", stock=0, min_stock=0)
    db.add(p)
    await db.flush()
    row = _listing(user_full.id, integ.id, title="x", sku=None)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await db.refresh(p)

    auth_as(user_full)
    r = await client.patch(
        f"/api/listings/{row.id}", json={"product_id": str(p.id), "sku": "SKU-1"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["product_id"] == str(p.id)
    assert body["sku"] == "SKU-1"


@pytest.mark.asyncio
async def test_patch_listing_invalid_status(
    client, auth_as, db, user_full
):
    integ = await _make_integration(db, user_full)
    row = _listing(user_full.id, integ.id)
    db.add(row)
    await db.commit()
    await db.refresh(row)

    auth_as(user_full)
    r = await client.patch(f"/api/listings/{row.id}", json={"status": "bogus"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_patch_listing_unknown_product(
    client, auth_as, db, user_full
):
    integ = await _make_integration(db, user_full)
    row = _listing(user_full.id, integ.id)
    db.add(row)
    await db.commit()
    await db.refresh(row)

    auth_as(user_full)
    bogus = str(uuid.uuid4())
    r = await client.patch(f"/api/listings/{row.id}", json={"product_id": bogus})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_listing(
    client, auth_as, db, user_full
):
    integ = await _make_integration(db, user_full)
    row = _listing(user_full.id, integ.id)
    db.add(row)
    await db.commit()
    await db.refresh(row)

    auth_as(user_full)
    r = await client.delete(f"/api/listings/{row.id}")
    assert r.status_code == 204
    r2 = await client.get(f"/api/listings/{row.id}")
    assert r2.status_code == 404


# ------------------------------------------------------------------- import


@pytest.mark.asyncio
async def test_import_creates_pending_job(
    client, auth_as, db, user_full, monkeypatch
):
    integ = await _make_integration(db, user_full)

    class _FakePool:
        async def enqueue_job(self, *a, **kw):
            class _R:
                job_id = "fake-arq"

            return _R()

    async def _fake_pool():
        return _FakePool()

    import app.routers.listings as lst
    monkeypatch.setattr(lst, "get_arq_pool", _fake_pool)

    auth_as(user_full)
    r = await client.post(
        "/api/listings/import", json={"integration_id": str(integ.id)}
    )
    assert r.status_code == 201, r.text
    job_id = r.json()["job_id"]
    assert job_id


@pytest.mark.asyncio
async def test_import_unsupported_platform(
    client, auth_as, db, user_full, monkeypatch
):
    integ = await _make_integration(db, user_full, platform=IntegrationPlatform.BLING)

    auth_as(user_full)
    r = await client.post(
        "/api/listings/import", json={"integration_id": str(integ.id)}
    )
    assert r.status_code == 501
    assert r.json()["detail"]["code"] == "platform_not_supported"


@pytest.mark.asyncio
async def test_import_other_users_integration_404(
    client, auth_as, db, user_full, other_user
):
    other_integ = await _make_integration(db, other_user)
    auth_as(user_full)
    r = await client.post(
        "/api/listings/import", json={"integration_id": str(other_integ.id)}
    )
    assert r.status_code == 404


# ------------------------------------------------------------------- requests


@pytest.mark.asyncio
async def test_listing_request_crud(client, auth_as, db, user_full):
    auth_as(user_full)
    r = await client.post(
        "/api/listing-requests",
        json={
            "platform": "shopee",
            "product_name": "New Widget",
            "sku": "NEW-1",
            "requested_price": 1990,
        },
    )
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    assert r.json()["status"] == "pending"

    r = await client.get("/api/listing-requests")
    assert len(r.json()) == 1

    r = await client.patch(
        f"/api/listing-requests/{rid}", json={"status": "completed"}
    )
    assert r.status_code == 200
    assert r.json()["status"] == "completed"

    r = await client.get("/api/listing-requests?status=pending")
    assert r.json() == []

    r = await client.delete(f"/api/listing-requests/{rid}")
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_listing_request_invalid_platform(client, auth_as, user_full):
    auth_as(user_full)
    r = await client.post(
        "/api/listing-requests",
        json={"platform": "bogus", "product_name": "x"},
    )
    assert r.status_code == 400


# ------------------------------------------------------------- permissions gate


@pytest.mark.asyncio
async def test_listings_view_requires_anuncios_permission(
    client, auth_as, db
):
    u = User(
        open_id=f"email:np-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"np-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions={},
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)

    auth_as(u)
    r = await client.get("/api/listings")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_listings_require_auth(client):
    r = await client.get("/api/listings")
    assert r.status_code == 401
