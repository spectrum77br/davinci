"""product_links router: batch delete (Item 4) + end-to-end reload reconcile
(Item 2) through POST /api/sync/product/{id}."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
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
from app.services.marketplaces.base import SyncResult, SyncStatus
from app.services.marketplaces.bling import BlingClient


@pytest_asyncio.fixture
async def admin(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:pl-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"pl-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _integration(
    db: AsyncSession, user: User, platform: IntegrationPlatform, marketplace: Marketplace
) -> Integration:
    company = Company(razao_social="ACME", apelido=f"acme-{uuid.uuid4().hex[:6]}")
    db.add(company)
    await db.flush()
    store = Store(
        company_id=company.id, marketplace=marketplace,
        status=StoreStatus.ACTIVE, bling_store_id=1,
    )
    db.add(store)
    await db.flush()
    integ = Integration(
        user_id=user.id,
        store_id=store.id,
        platform=platform,
        name=f"{platform.value}-acc",
        credentials=encrypt_json({"access_token": "x", "expires_at": 9999999999}),
    )
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    return integ


# ------------------------------------------------------------ Item 4: bulk delete


@pytest.mark.asyncio
async def test_bulk_delete_product_links(
    db: AsyncSession, admin: User, client: AsyncClient, auth_as
):
    auth_as(admin)
    integ = await _integration(db, admin, IntegrationPlatform.SHOPEE, Marketplace.SHOPEE)
    p = Product(user_id=admin.id, sku="sku-bulk", name="p", stock=0, min_stock=0)
    db.add(p)
    await db.flush()
    links = [
        ProductLink(
            user_id=admin.id, product_id=p.id, integration_id=integ.id,
            store_id=integ.store_id, platform=IntegrationPlatform.SHOPEE,
            external_id=f"{i}", variation_id=f"{i}0", external_sku="sku-bulk",
            last_sync_status=LinkSyncStatus.OK,
        )
        for i in range(3)
    ]
    db.add_all(links)
    await db.commit()
    for link in links:
        await db.refresh(link)

    to_delete = [str(links[0].id), str(links[1].id)]
    r = await client.post("/api/product-links/bulk-delete", json={"link_ids": to_delete})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] == 2
    assert set(body["deleted_ids"]) == set(to_delete)

    remaining = (await db.execute(select(ProductLink.id))).scalars().all()
    assert [str(x) for x in remaining] == [str(links[2].id)]


@pytest.mark.asyncio
async def test_bulk_delete_empty_list_rejected(
    db: AsyncSession, admin: User, client: AsyncClient, auth_as
):
    auth_as(admin)
    r = await client.post("/api/product-links/bulk-delete", json={"link_ids": []})
    assert r.status_code == 422  # min_length=1


# ------------------------------------------------- Item 2: reconcile via endpoint


class _FakeBling(BlingClient):
    def __init__(self):
        super().__init__(
            {"client_id": "x", "client_secret": "y", "access_token": "t",
             "refresh_token": "r", "expires_at": 9999999999}
        )

    async def get_product_stock_smart(self, bling_product_id: int, sku=None) -> dict:
        return {"stock": 5, "raw": {}, "found_via": "id"}


class _FakeShopee:
    async def get_listing_snapshot(self, link) -> dict:
        return {"sku": "dg053.ci", "title": "titulo novo"}

    async def update_stock(self, link, qty, *, bling_store_id=None, force=False) -> SyncResult:
        return SyncResult(status=SyncStatus.OK, qty_before=link.stock, qty_after=qty)


def _fake_client_for(platform, creds, on_token_refresh=None, integration_id=None):
    if platform == IntegrationPlatform.BLING:
        return _FakeBling()
    return _FakeShopee()


@pytest.mark.asyncio
async def test_sync_product_reload_reconciles_moved_sku(
    db: AsyncSession, admin: User, client: AsyncClient, auth_as, monkeypatch
):
    """Reload on the OLD product (dg053.sp) when the listing's seller_sku now
    reads dg053.ci: the shopee link must move onto the new product and the
    response must report the move."""
    auth_as(admin)
    bling = await _integration(db, admin, IntegrationPlatform.BLING, Marketplace.ML)
    shopee = await _integration(
        db, admin, IntegrationPlatform.SHOPEE, Marketplace.SHOPEE
    )

    old = Product(
        user_id=admin.id, sku="dg053.sp", name="old", stock=0, min_stock=0, bling_product_id=1001
    )
    new = Product(
        user_id=admin.id, sku="dg053.ci", name="new", stock=0, min_stock=0, bling_product_id=1002
    )
    db.add_all([old, new])
    await db.flush()

    # bling self-links + the shopee link on the OLD product
    db.add_all([
        ProductLink(
            user_id=admin.id, product_id=old.id, integration_id=bling.id,
            store_id=bling.store_id, platform=IntegrationPlatform.BLING,
            external_id="1001", external_sku="dg053.sp", last_sync_status=LinkSyncStatus.OK,
        ),
        ProductLink(
            user_id=admin.id, product_id=new.id, integration_id=bling.id,
            store_id=bling.store_id, platform=IntegrationPlatform.BLING,
            external_id="1002", external_sku="dg053.ci", last_sync_status=LinkSyncStatus.OK,
        ),
    ])
    shopee_link = ProductLink(
        user_id=admin.id, product_id=old.id, integration_id=shopee.id,
        store_id=shopee.store_id, platform=IntegrationPlatform.SHOPEE,
        external_id="111", variation_id="222", external_sku="dg053.sp",
        listing_title="titulo antigo", stock=0, last_sync_status=LinkSyncStatus.OK,
    )
    db.add(shopee_link)
    await db.commit()
    await db.refresh(shopee_link)

    monkeypatch.setattr("app.services.link_reconcile.client_for", _fake_client_for)
    monkeypatch.setattr("app.services.sync_orchestrator.client_for", _fake_client_for)

    r = await client.post(f"/api/sync/product/{old.id}", json={})
    assert r.status_code == 200, r.text
    body = r.json()

    moves = [d for d in body["details"] if d.get("kind") == "reconcile_move"]
    assert len(moves) == 1
    assert moves[0]["from_sku"] == "dg053.sp"
    assert moves[0]["to_sku"] == "dg053.ci"
    assert body["result"]["reconcile"]["moved"] == 1

    await db.refresh(shopee_link)
    assert shopee_link.product_id == new.id
    assert shopee_link.external_sku == "dg053.ci"
    assert shopee_link.listing_title == "titulo novo"


@pytest.mark.asyncio
async def test_sync_product_reconcile_disabled_leaves_link(
    db: AsyncSession, admin: User, client: AsyncClient, auth_as, monkeypatch
):
    """reconcile=false → pure stock push, no re-pointing."""
    auth_as(admin)
    bling = await _integration(db, admin, IntegrationPlatform.BLING, Marketplace.ML)
    shopee = await _integration(
        db, admin, IntegrationPlatform.SHOPEE, Marketplace.SHOPEE
    )

    old = Product(
        user_id=admin.id, sku="dg053.sp", name="old", stock=0, min_stock=0, bling_product_id=1001
    )
    new = Product(
        user_id=admin.id, sku="dg053.ci", name="new", stock=0, min_stock=0, bling_product_id=1002
    )
    db.add_all([old, new])
    await db.flush()
    db.add(ProductLink(
        user_id=admin.id, product_id=old.id, integration_id=bling.id,
        store_id=bling.store_id, platform=IntegrationPlatform.BLING,
        external_id="1001", external_sku="dg053.sp", last_sync_status=LinkSyncStatus.OK,
    ))
    shopee_link = ProductLink(
        user_id=admin.id, product_id=old.id, integration_id=shopee.id,
        store_id=shopee.store_id, platform=IntegrationPlatform.SHOPEE,
        external_id="111", variation_id="222", external_sku="dg053.sp",
        stock=0, last_sync_status=LinkSyncStatus.OK,
    )
    db.add(shopee_link)
    await db.commit()
    await db.refresh(shopee_link)

    monkeypatch.setattr("app.services.link_reconcile.client_for", _fake_client_for)
    monkeypatch.setattr("app.services.sync_orchestrator.client_for", _fake_client_for)

    r = await client.post(f"/api/sync/product/{old.id}", json={"reconcile": False})
    assert r.status_code == 200, r.text
    assert [d for d in r.json()["details"] if d.get("kind") == "reconcile_move"] == []

    await db.refresh(shopee_link)
    assert shopee_link.product_id == old.id  # unchanged
