"""On-demand SKU reconcile (services.link_reconcile).

Scenario: the operator changes the seller_sku ON the marketplace listing from
dg053.sp → dg053.ci. Clicking "recarregar" on the OLD product must MOVE the
link (re-point product_id/external_sku/listing_title) onto the product that
owns the new SKU — not delete/recreate it.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
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
from app.services.link_reconcile import reconcile_product_links


@pytest_asyncio.fixture
async def user(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:rc-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"rc-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _shopee_integration(db: AsyncSession, user: User) -> Integration:
    company = Company(razao_social="ACME", apelido=f"acme-{uuid.uuid4().hex[:6]}")
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


async def _shopee_link(
    db: AsyncSession, user: User, integ: Integration, product: Product, *, sku: str
) -> ProductLink:
    link = ProductLink(
        user_id=user.id,
        product_id=product.id,
        integration_id=integ.id,
        store_id=integ.store_id,
        platform=IntegrationPlatform.SHOPEE,
        external_id="111",
        variation_id="222",
        external_sku=sku,
        listing_title="titulo antigo",
        stock=0,
        last_sync_status=LinkSyncStatus.OK,
    )
    db.add(link)
    await db.commit()
    await db.refresh(link)
    return link


def _snapshot_returning(sku: str | None, title: str | None = "titulo novo"):
    async def _snap(self, link):  # noqa: ANN001
        return {"sku": sku, "title": title} if sku is not None else None
    return _snap


@pytest.mark.asyncio
async def test_reconcile_moves_link_to_new_sku_product(
    db: AsyncSession, user: User, monkeypatch
):
    old = Product(user_id=user.id, sku="dg053.sp", name="old", stock=0, min_stock=0)
    new = Product(user_id=user.id, sku="dg053.ci", name="new", stock=5, min_stock=0)
    db.add_all([old, new])
    await db.flush()
    integ = await _shopee_integration(db, user)
    link = await _shopee_link(db, user, integ, old, sku="dg053.sp")

    from app.services.marketplaces import shopee as shopee_mod
    monkeypatch.setattr(
        shopee_mod.ShopeeClient, "get_listing_snapshot", _snapshot_returning("dg053.ci")
    )

    report = await reconcile_product_links(db, user=user, product=old)
    await db.commit()

    assert report.checked == 1
    assert len(report.moves) == 1
    m = report.moves[0]
    assert m.from_sku == "dg053.sp" and m.to_sku == "dg053.ci"
    assert m.to_product_id == new.id

    await db.refresh(link)
    assert link.product_id == new.id          # moved (re-pointed, not recreated)
    assert link.external_sku == "dg053.ci"
    assert link.listing_title == "titulo novo"

    # exactly one link still exists (no dup created)
    links = (await db.execute(select(ProductLink))).scalars().all()
    assert len(links) == 1


@pytest.mark.asyncio
async def test_reconcile_noop_when_sku_unchanged(
    db: AsyncSession, user: User, monkeypatch
):
    p = Product(user_id=user.id, sku="dg053.sp", name="p", stock=3, min_stock=0)
    db.add(p)
    await db.flush()
    integ = await _shopee_integration(db, user)
    link = await _shopee_link(db, user, integ, p, sku="dg053.sp")

    from app.services.marketplaces import shopee as shopee_mod
    # listing still carries the same SKU (case/space variant on purpose)
    monkeypatch.setattr(
        shopee_mod.ShopeeClient, "get_listing_snapshot", _snapshot_returning(" DG053.SP ")
    )

    report = await reconcile_product_links(db, user=user, product=p)
    assert report.checked == 1
    assert report.moves == []
    await db.refresh(link)
    assert link.product_id == p.id


@pytest.mark.asyncio
async def test_reconcile_warns_when_new_sku_has_no_product(
    db: AsyncSession, user: User, monkeypatch
):
    old = Product(user_id=user.id, sku="dg053.sp", name="old", stock=0, min_stock=0)
    db.add(old)
    await db.flush()
    integ = await _shopee_integration(db, user)
    link = await _shopee_link(db, user, integ, old, sku="dg053.sp")

    from app.services.marketplaces import shopee as shopee_mod
    monkeypatch.setattr(
        shopee_mod.ShopeeClient, "get_listing_snapshot", _snapshot_returning("dg999.zz")
    )

    report = await reconcile_product_links(db, user=user, product=old)
    assert report.moves == []
    assert len(report.warnings) == 1
    assert report.warnings[0]["code"] == "produto_novo_ausente"
    assert report.warnings[0]["sku"] == "dg999.zz"
    await db.refresh(link)
    assert link.product_id == old.id  # left untouched (not zeroed)


@pytest.mark.asyncio
async def test_reconcile_ambiguous_new_sku_does_not_move(
    db: AsyncSession, user: User, monkeypatch
):
    old = Product(user_id=user.id, sku="dg053.sp", name="old", stock=0, min_stock=0)
    dup_a = Product(user_id=user.id, sku="dg053.ci", name="a", stock=1, min_stock=0)
    dup_b = Product(user_id=user.id, sku="DG053.CI", name="b", stock=1, min_stock=0)
    db.add_all([old, dup_a, dup_b])
    await db.flush()
    integ = await _shopee_integration(db, user)
    link = await _shopee_link(db, user, integ, old, sku="dg053.sp")

    from app.services.marketplaces import shopee as shopee_mod
    monkeypatch.setattr(
        shopee_mod.ShopeeClient, "get_listing_snapshot", _snapshot_returning("dg053.ci")
    )

    report = await reconcile_product_links(db, user=user, product=old)
    assert report.moves == []
    assert len(report.warnings) == 1
    assert report.warnings[0]["code"] == "sku_ambiguo"
    await db.refresh(link)
    assert link.product_id == old.id


@pytest.mark.asyncio
async def test_reconcile_unreadable_snapshot_leaves_link(
    db: AsyncSession, user: User, monkeypatch
):
    old = Product(user_id=user.id, sku="dg053.sp", name="old", stock=0, min_stock=0)
    new = Product(user_id=user.id, sku="dg053.ci", name="new", stock=5, min_stock=0)
    db.add_all([old, new])
    await db.flush()
    integ = await _shopee_integration(db, user)
    link = await _shopee_link(db, user, integ, old, sku="dg053.sp")

    from app.services.marketplaces import shopee as shopee_mod
    monkeypatch.setattr(
        shopee_mod.ShopeeClient, "get_listing_snapshot", _snapshot_returning(None)
    )

    report = await reconcile_product_links(db, user=user, product=old)
    assert report.moves == []
    assert report.unreadable == 1
    await db.refresh(link)
    assert link.product_id == old.id  # never guessed / zeroed


@pytest.mark.asyncio
async def test_reconcile_respects_integration_filter(
    db: AsyncSession, user: User, monkeypatch
):
    """only_integration_ids scopes which links are reconciled."""
    old = Product(user_id=user.id, sku="dg053.sp", name="old", stock=0, min_stock=0)
    new = Product(user_id=user.id, sku="dg053.ci", name="new", stock=5, min_stock=0)
    db.add_all([old, new])
    await db.flush()
    integ = await _shopee_integration(db, user)
    link = await _shopee_link(db, user, integ, old, sku="dg053.sp")

    from app.services.marketplaces import shopee as shopee_mod
    monkeypatch.setattr(
        shopee_mod.ShopeeClient, "get_listing_snapshot", _snapshot_returning("dg053.ci")
    )

    other_integ_id = uuid.uuid4()
    report = await reconcile_product_links(
        db, user=user, product=old, only_integration_ids=[other_integ_id]
    )
    assert report.checked == 0  # the shopee link's integration wasn't in scope
    await db.refresh(link)
    assert link.product_id == old.id
