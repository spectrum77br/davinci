"""Pricing catalog endpoints + batch push (Fase 9c)."""

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
    Department,
    Integration,
    IntegrationPlatform,
    Listing,
    ListingStatus,
    Marketplace,
    PricingAccount,
    PricingPlatform,
    PricingProduct,
    Store,
    StoreStatus,
    User,
    UserRole,
    UserSettings,
    UserStatus,
)
from app.security.cipher import encrypt_json
from app.services.marketplaces.ml import ML_API_BASE
from app.services.pricing.batch import run_push_prices_batch

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
        open_id=f"email:c-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"c-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions=PERM_FULL,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def catalog_setup(db: AsyncSession, user_full: User) -> dict[str, Any]:
    """One ML integration + 2 products (one in_catalog) + 2 listings + 2
    accounts (one with department=catalogo)."""
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

    listings = [
        Listing(
            user_id=user_full.id,
            integration_id=integ.id,
            platform=IntegrationPlatform.ML,
            external_id=f"MLB{i}",
            sku=sku,
            title=f"item {i}",
            status=ListingStatus.ACTIVE,
            price=10000,
        )
        for i, sku in enumerate(["CAT-1", "REG-1"], start=1)
    ]
    for li in listings:
        db.add(li)

    p_cat = PricingProduct(
        user_id=user_full.id,
        sku="CAT-1",
        name="Cat product",
        cost_kit1=Decimal("100.00"),
        in_catalog=True,
        department=Department.CATALOGO,
    )
    p_reg = PricingProduct(
        user_id=user_full.id,
        sku="REG-1",
        name="Reg product",
        cost_kit1=Decimal("50.00"),
        in_catalog=False,
    )
    a_cat = PricingAccount(
        user_id=user_full.id,
        name="cat-acc",
        platform=PricingPlatform.ML,
        kit_number=1,
        commission=Decimal("0.10"),
        margin1=Decimal("0.20"),
        shipping1=Decimal("5.00"),
        integration_id=integ.id,
        department=Department.CATALOGO,
    )
    a_reg = PricingAccount(
        user_id=user_full.id,
        name="reg-acc",
        platform=PricingPlatform.ML,
        kit_number=1,
        commission=Decimal("0.10"),
        margin1=Decimal("0.20"),
        shipping1=Decimal("5.00"),
        integration_id=integ.id,
    )
    db.add_all([p_cat, p_reg, a_cat, a_reg])
    await db.commit()
    for o in [p_cat, p_reg, a_cat, a_reg]:
        await db.refresh(o)
    return {
        "integration": integ,
        "p_cat": p_cat,
        "p_reg": p_reg,
        "a_cat": a_cat,
        "a_reg": a_reg,
    }


# =================================================== catalog-listings


@pytest.mark.asyncio
async def test_catalog_listings_returns_only_in_catalog_skus(
    client: AsyncClient,
    user_full: User,
    catalog_setup: dict[str, Any],
    auth_as: Callable[[User | None], None],
):
    auth_as(user_full)
    r = await client.get("/api/pricing/catalog-listings")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["sku"] == "CAT-1"


@pytest.mark.asyncio
async def test_catalog_listings_no_in_catalog_returns_empty(
    db: AsyncSession,
    client: AsyncClient,
    user_full: User,
    catalog_setup: dict[str, Any],
    auth_as: Callable[[User | None], None],
):
    auth_as(user_full)
    catalog_setup["p_cat"].in_catalog = False
    await db.commit()
    r = await client.get("/api/pricing/catalog-listings")
    assert r.json() == []


# =================================================== push-catalog (B14)


@pytest.mark.asyncio
async def test_push_catalog_refuses_non_catalog_product(
    client: AsyncClient,
    user_full: User,
    catalog_setup: dict[str, Any],
    auth_as: Callable[[User | None], None],
):
    """B14 — push-catalog must not touch produtos sem in_catalog."""
    auth_as(user_full)
    r = await client.post(
        "/api/pricing/push-catalog",
        json={
            "items": [
                {
                    "pricing_account_id": str(catalog_setup["a_cat"].id),
                    "pricing_product_id": str(catalog_setup["p_reg"].id),
                }
            ]
        },
    )
    assert r.status_code == 200
    item = r.json()["results"][0]
    assert item["ok"] is False
    assert item["code"] == "not_in_catalog"


@pytest.mark.asyncio
async def test_push_catalog_refuses_non_catalog_account(
    client: AsyncClient,
    user_full: User,
    catalog_setup: dict[str, Any],
    auth_as: Callable[[User | None], None],
):
    auth_as(user_full)
    r = await client.post(
        "/api/pricing/push-catalog",
        json={
            "items": [
                {
                    "pricing_account_id": str(catalog_setup["a_reg"].id),
                    "pricing_product_id": str(catalog_setup["p_cat"].id),
                }
            ]
        },
    )
    item = r.json()["results"][0]
    assert item["ok"] is False
    assert item["code"] == "account_not_catalog"


@pytest.mark.asyncio
async def test_push_catalog_success(
    client: AsyncClient,
    user_full: User,
    catalog_setup: dict[str, Any],
    auth_as: Callable[[User | None], None],
):
    auth_as(user_full)
    with respx.mock(base_url=ML_API_BASE) as router:
        put_route = router.put("/items/MLB1").mock(
            return_value=Response(200, json={"id": "MLB1"})
        )
        r = await client.post(
            "/api/pricing/push-catalog",
            json={
                "items": [
                    {
                        "pricing_account_id": str(catalog_setup["a_cat"].id),
                        "pricing_product_id": str(catalog_setup["p_cat"].id),
                    }
                ]
            },
        )
        item = r.json()["results"][0]
        assert item["ok"] is True
        assert put_route.called


# =================================================== batch service


@pytest.mark.asyncio
async def test_batch_service_records_progress_and_skips_telegram_when_no_chat(
    db: AsyncSession,
    user_full: User,
    catalog_setup: dict[str, Any],
    monkeypatch,
):
    from app.models import (
        BackgroundJob,
        BackgroundJobStatus,
        BackgroundJobType,
    )

    job = BackgroundJob(
        type=BackgroundJobType.PUSH_PRICES_BATCH,
        status=BackgroundJobStatus.PENDING,
        created_by=user_full.id,
        payload={"count": 1},
        total=1,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # No UserSettings row → no telegram_chat_id → telegram skipped silently.
    with respx.mock(base_url=ML_API_BASE) as router:
        router.put("/items/MLB1").mock(
            return_value=Response(200, json={"id": "MLB1"})
        )
        await run_push_prices_batch(
            db,
            job_id=job.id,
            user_id=user_full.id,
            items=[
                {
                    "pricing_account_id": str(catalog_setup["a_cat"].id),
                    "pricing_product_id": str(catalog_setup["p_cat"].id),
                }
            ],
            idempotency_prefix="batch-test-1",
            notify_telegram=True,
        )

    await db.refresh(job)
    assert job.status == BackgroundJobStatus.SUCCEEDED
    assert job.processed == 1
    assert job.total == 1
    assert job.result["summary"]["ok"] == 1
    assert job.result["summary"]["failed"] == 0


@pytest.mark.asyncio
async def test_batch_service_sends_telegram_when_chat_configured(
    db: AsyncSession,
    user_full: User,
    catalog_setup: dict[str, Any],
):
    from app.models import (
        BackgroundJob,
        BackgroundJobStatus,
        BackgroundJobType,
    )

    db.add(
        UserSettings(
            user_id=user_full.id,
            telegram_chat_id="999999",
        )
    )
    await db.commit()

    job = BackgroundJob(
        type=BackgroundJobType.PUSH_PRICES_BATCH,
        status=BackgroundJobStatus.PENDING,
        created_by=user_full.id,
        payload={},
        total=1,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    import os

    # Force-overwrite — env_file may inject an empty value that setdefault honors.
    os.environ["TELEGRAM_BOT_TOKEN"] = "fake-token"
    from app.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]

    from app.services import telegram as tg_mod

    with respx.mock() as router:
        router.put(f"{ML_API_BASE}/items/MLB1").mock(
            return_value=Response(200, json={"id": "MLB1"})
        )
        tg_route = router.post(
            f"{tg_mod.TELEGRAM_API_BASE}/botfake-token/sendMessage"
        ).mock(return_value=Response(200, json={"ok": True, "result": {}}))
        await run_push_prices_batch(
            db,
            job_id=job.id,
            user_id=user_full.id,
            items=[
                {
                    "pricing_account_id": str(catalog_setup["a_cat"].id),
                    "pricing_product_id": str(catalog_setup["p_cat"].id),
                }
            ],
            idempotency_prefix="batch-tg",
            notify_telegram=True,
        )
        assert tg_route.called

    await db.refresh(job)
    assert job.status == BackgroundJobStatus.SUCCEEDED


# =================================================== push-batch endpoint


@pytest.mark.asyncio
async def test_push_batch_endpoint_creates_job(
    db: AsyncSession,
    client: AsyncClient,
    user_full: User,
    catalog_setup: dict[str, Any],
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    auth_as(user_full)

    enqueued: dict = {}

    class FakePool:
        async def enqueue_job(self, fn_name, *args, **kwargs):
            enqueued["fn"] = fn_name
            enqueued["args"] = args

            class _J:
                job_id = "arq-batch-1"

            return _J()

    async def fake_pool():
        return FakePool()

    import app.routers.pricing as pricing_mod

    monkeypatch.setattr(pricing_mod, "get_arq_pool", fake_pool)

    r = await client.post(
        "/api/pricing/push-batch?notify_telegram=false",
        json={
            "items": [
                {
                    "pricing_account_id": str(catalog_setup["a_cat"].id),
                    "pricing_product_id": str(catalog_setup["p_cat"].id),
                }
            ]
        },
    )
    assert r.status_code == 201
    assert enqueued["fn"] == "push_prices_batch_run"

    from app.models import BackgroundJob, BackgroundJobType

    rows = (
        await db.execute(
            select(BackgroundJob).where(
                BackgroundJob.type == BackgroundJobType.PUSH_PRICES_BATCH
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].arq_job_id == "arq-batch-1"
    assert rows[0].total == 1


@pytest.mark.asyncio
async def test_push_batch_empty_returns_400(
    client: AsyncClient,
    user_full: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(user_full)
    r = await client.post("/api/pricing/push-batch", json={"items": []})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "empty_batch"


# =================================================== push-report manual


@pytest.mark.asyncio
async def test_push_report_no_chat_returns_400(
    client: AsyncClient,
    user_full: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(user_full)
    import os

    # Remove env so resolve fails
    os.environ.pop("TELEGRAM_CHAT_ID", None)
    from app.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]

    r = await client.post("/api/pricing/push-report", json={"summary": "hi"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "telegram_not_configured"
