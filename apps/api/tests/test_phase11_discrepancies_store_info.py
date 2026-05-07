"""Phase 11 tests — discrepancies endpoint + store_info setDepartment."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Company,
    Department,
    Integration,
    IntegrationPlatform,
    Listing,
    ListingStatus,
    Marketplace,
    PricingAccount,
    PricingPlatform,
    Product,
    Store,
    StoreInfo,
    StoreStatus,
    User,
    UserRole,
    UserStatus,
)

PERM = {
    "tabela_precos": {"view": True, "edit": True, "delete": True},
    "anuncios": {"view": True, "edit": True, "delete": True},
}


@pytest_asyncio.fixture
async def user_p11(db: AsyncSession) -> User:
    email = f"p11-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(
        open_id=f"email:{email}",
        email=email,
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions=PERM,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


# =========================================================== setDepartment


@pytest.mark.asyncio
async def test_store_info_set_department_creates_pricing_account(
    db: AsyncSession,
    client: AsyncClient,
    user_p11: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(user_p11)

    info = StoreInfo(
        user_id=user_p11.id,
        platform="shopee",
        account_name="Loja Shopee 1",
    )
    db.add(info)
    await db.commit()
    await db.refresh(info)

    r = await client.post(
        f"/api/pricing/store-info/{info.id}/department",
        json={"department": "celular"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["department"] == "celular"
    assert data["platform"] == "shopee"
    assert data["store_info_id"] == str(info.id)
    first_id = data["id"]

    # Idempotent: same (store_info, department) returns the same row
    r = await client.post(
        f"/api/pricing/store-info/{info.id}/department",
        json={"department": "celular"},
    )
    assert r.status_code == 200
    assert r.json()["id"] == first_id

    # Different department → new row
    r = await client.post(
        f"/api/pricing/store-info/{info.id}/department",
        json={"department": "mala"},
    )
    assert r.status_code == 200
    assert r.json()["id"] != first_id

    rows = (
        await db.execute(
            select(PricingAccount).where(PricingAccount.store_info_id == info.id)
        )
    ).scalars().all()
    assert {r.department for r in rows} == {Department.CELULAR, Department.MALA}
    assert {r.platform for r in rows} == {PricingPlatform.SHOPEE}


@pytest.mark.asyncio
async def test_store_info_set_department_invalid_department(
    db: AsyncSession,
    client: AsyncClient,
    user_p11: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(user_p11)
    info = StoreInfo(user_id=user_p11.id, platform="mercadolivre")
    db.add(info)
    await db.commit()
    await db.refresh(info)

    r = await client.post(
        f"/api/pricing/store-info/{info.id}/department",
        json={"department": "ufo"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_department"


@pytest.mark.asyncio
async def test_store_info_set_department_unsupported_platform(
    db: AsyncSession,
    client: AsyncClient,
    user_p11: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(user_p11)
    info = StoreInfo(user_id=user_p11.id, platform="freeform-x")
    db.add(info)
    await db.commit()
    await db.refresh(info)

    r = await client.post(
        f"/api/pricing/store-info/{info.id}/department",
        json={"department": "celular"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "store_info_platform_unsupported"


@pytest.mark.asyncio
async def test_store_info_set_department_not_found(
    db: AsyncSession,
    client: AsyncClient,
    user_p11: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(user_p11)
    r = await client.post(
        f"/api/pricing/store-info/{uuid.uuid4()}/department",
        json={"department": "celular"},
    )
    assert r.status_code == 404


# =========================================================== discrepancies


@pytest_asyncio.fixture
async def discrep_setup(db: AsyncSession, user_p11: User) -> dict[str, object]:
    company = Company(razao_social="ACME", apelido="acme")
    db.add(company)
    await db.flush()
    store = Store(
        company_id=company.id, marketplace=Marketplace.ML, status=StoreStatus.ACTIVE
    )
    db.add(store)
    await db.flush()
    integ = Integration(
        user_id=user_p11.id,
        store_id=store.id,
        platform=IntegrationPlatform.ML,
        name="acme-ml",
        credentials=b"x",
    )
    db.add(integ)
    await db.flush()

    p_ok = Product(user_id=user_p11.id, sku="OK-1", name="ok", stock=10)
    p_diff = Product(user_id=user_p11.id, sku="DIFF-1", name="diff", stock=10)
    p_big = Product(user_id=user_p11.id, sku="BIG-1", name="big", stock=10)
    db.add_all([p_ok, p_diff, p_big])
    await db.flush()

    db.add_all(
        [
            Listing(
                user_id=user_p11.id,
                integration_id=integ.id,
                platform=IntegrationPlatform.ML,
                external_id="MLB-1",
                sku="OK-1",
                title="ok listing",
                stock=10,
                product_id=p_ok.id,
                status=ListingStatus.ACTIVE,
            ),
            Listing(
                user_id=user_p11.id,
                integration_id=integ.id,
                platform=IntegrationPlatform.ML,
                external_id="MLB-2",
                sku="DIFF-1",
                title="diff listing",
                stock=8,
                product_id=p_diff.id,
                status=ListingStatus.ACTIVE,
            ),
            Listing(
                user_id=user_p11.id,
                integration_id=integ.id,
                platform=IntegrationPlatform.ML,
                external_id="MLB-3",
                sku="BIG-1",
                title="big diff listing",
                stock=0,
                product_id=p_big.id,
                status=ListingStatus.ACTIVE,
            ),
            Listing(
                user_id=user_p11.id,
                integration_id=integ.id,
                platform=IntegrationPlatform.ML,
                external_id="MLB-ORPH",
                sku="ORPH",
                title="unlinked listing",
                stock=99,
                product_id=None,
                status=ListingStatus.ACTIVE,
            ),
        ]
    )
    await db.commit()
    return {"integration_id": integ.id}


@pytest.mark.asyncio
async def test_discrepancies_lists_only_diffs(
    client: AsyncClient,
    user_p11: User,
    auth_as: Callable[[User | None], None],
    discrep_setup: dict[str, object],
):
    auth_as(user_p11)

    r = await client.get("/api/discrepancies")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    diffs = sorted(body["items"], key=lambda x: x["diff"], reverse=True)
    assert diffs[0]["sku"] == "BIG-1"
    assert diffs[0]["expected_stock"] == 10
    assert diffs[0]["actual_stock"] == 0
    assert diffs[0]["diff"] == 10
    assert diffs[1]["sku"] == "DIFF-1"
    assert diffs[1]["diff"] == 2


@pytest.mark.asyncio
async def test_discrepancies_min_diff_filter(
    client: AsyncClient,
    user_p11: User,
    auth_as: Callable[[User | None], None],
    discrep_setup: dict[str, object],
):
    auth_as(user_p11)

    r = await client.get("/api/discrepancies?min_diff=5")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["sku"] == "BIG-1"


@pytest.mark.asyncio
async def test_discrepancies_platform_and_integration_filters(
    client: AsyncClient,
    user_p11: User,
    auth_as: Callable[[User | None], None],
    discrep_setup: dict[str, object],
):
    auth_as(user_p11)

    r = await client.get("/api/discrepancies?platform=ml")
    assert r.status_code == 200
    assert r.json()["total"] == 2

    r = await client.get("/api/discrepancies?platform=shopee")
    assert r.status_code == 200
    assert r.json()["total"] == 0

    r = await client.get("/api/discrepancies?platform=banana")
    assert r.status_code == 400

    iid = discrep_setup["integration_id"]
    r = await client.get(f"/api/discrepancies?integration_id={iid}")
    assert r.status_code == 200
    assert r.json()["total"] == 2

    r = await client.get(f"/api/discrepancies?integration_id={uuid.uuid4()}")
    assert r.status_code == 200
    assert r.json()["total"] == 0
