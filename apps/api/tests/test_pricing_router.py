"""Pricing CRUD router (Fase 9a)."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    PricingAccount,
    PricingOverride,
    PricingProduct,
    User,
    UserRole,
    UserStatus,
)
from app.security.cipher import decrypt

PERM_VIEW = {
    "tabela_precos": {"view": True, "edit": False, "delete": False},
    "tabela_precos_contas": {"view": True, "edit": False, "delete": False},
    "tabela_precos_produtos": {"view": True, "edit": False, "delete": False},
}
PERM_FULL = {
    "tabela_precos": {"view": True, "edit": True, "delete": True},
    "tabela_precos_contas": {"view": True, "edit": True, "delete": True},
    "tabela_precos_produtos": {"view": True, "edit": True, "delete": True},
}


@pytest_asyncio.fixture
async def user_full(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:p-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"p-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions=PERM_FULL,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def user_view(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:pv-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"pv-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions=PERM_VIEW,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


# ============================================================== accounts


@pytest.mark.asyncio
async def test_create_account_encrypts_password(
    db: AsyncSession,
    client: AsyncClient,
    user_full: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(user_full)
    r = await client.post(
        "/api/pricing/accounts",
        json={
            "name": "Shopee LunoGO",
            "platform": "shopee",
            "department": "celular",
            "kit_number": 1,
            "margin1": "0.20",
            "shipping1": "5.00",
            "email": "luno@example.com",
            "password": "s3cr3t",
        },
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["has_password"] is True
    assert "password" not in data
    assert "password_enc" not in data

    row = (
        await db.execute(select(PricingAccount).where(PricingAccount.id == data["id"]))
    ).scalar_one()
    assert row.password_enc is not None
    assert row.password_enc != "s3cr3t"
    assert decrypt(row.password_enc) == "s3cr3t"


@pytest.mark.asyncio
async def test_list_accounts_filters_by_department_and_platform(
    client: AsyncClient,
    user_full: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(user_full)
    for plat, dept in [
        ("shopee", "celular"),
        ("mercadolivre", "celular"),
        ("shopee", "mala"),
    ]:
        r = await client.post(
            "/api/pricing/accounts",
            json={"name": f"acc-{plat}-{dept}", "platform": plat, "department": dept},
        )
        assert r.status_code == 201

    r = await client.get("/api/pricing/accounts?department=celular")
    assert {a["name"] for a in r.json()} == {"acc-shopee-celular", "acc-mercadolivre-celular"}

    r = await client.get("/api/pricing/accounts?platform=shopee")
    assert {a["name"] for a in r.json()} == {"acc-shopee-celular", "acc-shopee-mala"}


@pytest.mark.asyncio
async def test_patch_account_password_clear(
    db: AsyncSession,
    client: AsyncClient,
    user_full: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(user_full)
    r = await client.post(
        "/api/pricing/accounts",
        json={"name": "x", "platform": "shopee", "password": "abc"},
    )
    aid = r.json()["id"]
    r = await client.patch(f"/api/pricing/accounts/{aid}", json={"password": ""})
    assert r.status_code == 200
    assert r.json()["has_password"] is False
    row = (
        await db.execute(select(PricingAccount).where(PricingAccount.id == aid))
    ).scalar_one()
    assert row.password_enc is None


@pytest.mark.asyncio
async def test_view_only_user_cannot_create_account(
    client: AsyncClient,
    user_view: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(user_view)
    r = await client.post(
        "/api/pricing/accounts",
        json={"name": "nope", "platform": "shopee"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_invalid_platform_returns_400(
    client: AsyncClient,
    user_full: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(user_full)
    r = await client.post(
        "/api/pricing/accounts",
        json={"name": "x", "platform": "ebay"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_platform"


# ============================================================== products


@pytest.mark.asyncio
async def test_create_product_unique_sku_per_user(
    client: AsyncClient,
    user_full: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(user_full)
    r = await client.post(
        "/api/pricing/products",
        json={"sku": "ABC-1", "name": "iPhone 15", "cost_kit1": "100"},
    )
    assert r.status_code == 201
    r = await client.post(
        "/api/pricing/products",
        json={"sku": "ABC-1", "name": "duplicate", "cost_kit1": "200"},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "sku_exists"


@pytest.mark.asyncio
async def test_toggle_catalog(
    client: AsyncClient,
    user_full: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(user_full)
    r = await client.post(
        "/api/pricing/products",
        json={"sku": "TOGGLE-1", "name": "x", "cost_kit1": "10"},
    )
    pid = r.json()["id"]
    assert r.json()["in_catalog"] is False
    r = await client.post(f"/api/pricing/products/{pid}/catalog")
    assert r.json()["in_catalog"] is True
    r = await client.post(f"/api/pricing/products/{pid}/catalog")
    assert r.json()["in_catalog"] is False


@pytest.mark.asyncio
async def test_import_products_creates_and_updates(
    db: AsyncSession,
    client: AsyncClient,
    user_full: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(user_full)
    # seed one
    await client.post(
        "/api/pricing/products",
        json={"sku": "IMP-1", "name": "old", "cost_kit1": "10"},
    )
    r = await client.post(
        "/api/pricing/products/import",
        json={
            "items": [
                {"sku": "IMP-1", "name": "new-name", "cost_kit1": "20"},
                {"sku": "IMP-2", "name": "fresh", "cost_kit1": "30"},
                {"sku": "", "name": "skipme", "cost_kit1": "0"},
            ]
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body == {"created": 1, "updated": 1, "skipped": 1}

    rows = (
        await db.execute(
            select(PricingProduct).where(PricingProduct.user_id == user_full.id)
        )
    ).scalars().all()
    by_sku = {p.sku: p for p in rows}
    assert by_sku["IMP-1"].name == "new-name"
    assert by_sku["IMP-2"].name == "fresh"


# ============================================================== overrides


@pytest.mark.asyncio
async def test_upsert_override_then_set_status_then_delete(
    db: AsyncSession,
    client: AsyncClient,
    user_full: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(user_full)
    a = (
        await client.post(
            "/api/pricing/accounts",
            json={"name": "A", "platform": "shopee"},
        )
    ).json()
    p = (
        await client.post(
            "/api/pricing/products",
            json={"sku": "OVR-1", "name": "n", "cost_kit1": "10"},
        )
    ).json()

    # PUT — create
    r = await client.put(
        "/api/pricing/overrides",
        json={
            "pricing_product_id": p["id"],
            "pricing_account_id": a["id"],
            "price_override": "199.90",
            "cell_status": "manual",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["cell_status"] == "manual"
    assert str(body["price_override"]) == "199.90"

    # PUT again — update same row (UNIQUE preserved)
    r = await client.put(
        "/api/pricing/overrides",
        json={
            "pricing_product_id": p["id"],
            "pricing_account_id": a["id"],
            "price_override": "299.00",
            "cell_status": "locked",
        },
    )
    assert r.json()["cell_status"] == "locked"
    rows = (
        await db.execute(select(PricingOverride))
    ).scalars().all()
    assert len(rows) == 1

    # cell-status only
    r = await client.put(
        "/api/pricing/overrides/cell-status",
        json={
            "pricing_product_id": p["id"],
            "pricing_account_id": a["id"],
            "cell_status": "disabled",
        },
    )
    assert r.json()["cell_status"] == "disabled"

    # delete
    r = await client.delete(
        f"/api/pricing/overrides?pricing_product_id={p['id']}&pricing_account_id={a['id']}"
    )
    assert r.status_code == 204
    rows = (
        await db.execute(select(PricingOverride))
    ).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_user_isolation_account_not_found_for_other_user(
    db: AsyncSession,
    client: AsyncClient,
    user_full: User,
    auth_as: Callable[[User | None], None],
):
    other = User(
        open_id="email:other@davinci-test.com",
        email="other@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions=PERM_FULL,
    )
    db.add(other)
    await db.commit()
    await db.refresh(other)

    auth_as(other)
    r = await client.post(
        "/api/pricing/accounts",
        json={"name": "other-acct", "platform": "shopee"},
    )
    other_id = r.json()["id"]

    auth_as(user_full)
    r = await client.patch(
        f"/api/pricing/accounts/{other_id}",
        json={"name": "hijack"},
    )
    assert r.status_code == 404
