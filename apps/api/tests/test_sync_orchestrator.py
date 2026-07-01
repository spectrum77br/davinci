"""SyncOrchestrator tests (Fase 4a).

Cover the platform-of-sync layer with a mocked BlingClient. Real adapters for
ML/Shopee/Amazon land in 4b — here we only verify:
  - Bling refresh path writes back into product + product_link
  - Non-Bling platforms get `skipped/platform_not_implemented`
  - SyncLog rows are persisted
  - Advisory lock blocks concurrent runs
  - Job state moves PENDING → RUNNING → SUCCEEDED with correct totals
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

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
    LinkSyncStatus,
    Marketplace,
    Product,
    ProductLink,
    Store,
    StoreStatus,
    SyncLog,
    User,
    UserRole,
    UserStatus,
)
from app.security.cipher import encrypt_json
from app.services.advisory_lock import try_user_sync_lock
from app.services.marketplaces.bling import BlingClient
from app.services.sync_orchestrator import SyncOrchestrator


@pytest_asyncio.fixture
async def user(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:sync-{uuid.uuid4().hex[:8]}@davinci-test.com",
        email=f"sync-{uuid.uuid4().hex[:8]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions={},
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _make_company_store(db: AsyncSession, user: User) -> tuple[Company, Store]:
    company = Company(
        razao_social="ACME LTDA",
        cnpj=uuid.uuid4().hex[:14],
        apelido=f"acme-{uuid.uuid4().hex[:6]}",
        responsavel_id=user.id,
    )
    db.add(company)
    await db.flush()
    store = Store(
        company_id=company.id,
        marketplace=Marketplace.ML,
        status=StoreStatus.ACTIVE,
        bling_store_id=12345,
    )
    db.add(store)
    await db.commit()
    await db.refresh(store)
    return company, store


async def _make_integration(
    db: AsyncSession, user: User, store: Store, platform: IntegrationPlatform
) -> Integration:
    i = Integration(
        user_id=user.id,
        store_id=store.id,
        platform=platform,
        name=f"{platform.value}-test",
        credentials=encrypt_json(
            {
                "client_id": "x",
                "client_secret": "y",
                "access_token": "tok",
                "refresh_token": "ref",
                "expires_at": 9999999999,
            }
        ),
    )
    db.add(i)
    await db.commit()
    await db.refresh(i)
    return i


async def _make_product_with_link(
    db: AsyncSession,
    user: User,
    integration: Integration,
    store: Store,
    *,
    bling_product_id: int = 777,
    initial_stock: int = 0,
) -> tuple[Product, ProductLink]:
    p = Product(
        user_id=user.id,
        sku=f"sku-{uuid.uuid4().hex[:6]}",
        name="acme widget",
        stock=initial_stock,
        bling_product_id=bling_product_id,
    )
    db.add(p)
    await db.flush()
    link = ProductLink(
        user_id=user.id,
        product_id=p.id,
        integration_id=integration.id,
        store_id=store.id,
        platform=integration.platform,
        external_id=str(bling_product_id),
        stock=initial_stock,
        last_sync_status=LinkSyncStatus.PENDING,
    )
    db.add(link)
    await db.commit()
    await db.refresh(p)
    await db.refresh(link)
    return p, link


class _FakeBlingClient(BlingClient):
    """Subclass so `isinstance(client, BlingClient)` passes in the orchestrator;
    overrides `get_product` so no HTTP is made."""

    def __init__(self, stock_by_id: dict[int, int]):
        super().__init__(
            {
                "client_id": "x",
                "client_secret": "y",
                "access_token": "tok",
                "refresh_token": "ref",
                "expires_at": 9999999999,
            }
        )
        self._stock = stock_by_id

    async def get_product(self, bling_product_id: int) -> dict:
        s = self._stock.get(bling_product_id)
        if s is None:
            return {}
        return {
            "id": bling_product_id,
            "nome": "fake",
            "codigo": "sku-fake",
            "estoque": {"saldoVirtualTotal": s},
            "preco": 10.0,
        }


@pytest.mark.asyncio
async def test_orchestrator_bling_refresh_writes_stock(
    db: AsyncSession, user: User
) -> None:
    _, store = await _make_company_store(db, user)
    integ = await _make_integration(db, user, store, IntegrationPlatform.BLING)
    p, link = await _make_product_with_link(
        db, user, integ, store, bling_product_id=42, initial_stock=0
    )

    fake = _FakeBlingClient({42: 17})
    with patch(
        "app.services.sync_orchestrator.client_for", return_value=fake
    ):
        orch = SyncOrchestrator(db, user_id=user.id)
        report = await orch.run([p])

    await db.refresh(p)
    await db.refresh(link)

    assert report.ok == 1
    assert report.total_links == 1
    assert p.stock == 17
    assert link.stock == 17
    assert link.last_sync_status == LinkSyncStatus.OK
    assert link.last_sync_at is not None

    logs = (
        await db.execute(select(SyncLog).where(SyncLog.product_id == p.id))
    ).scalars().all()
    assert len(logs) == 1
    assert logs[0].status == LinkSyncStatus.OK
    assert logs[0].qty_before == 0
    assert logs[0].qty_after == 17
    assert logs[0].action.value == "refresh_bling"


@pytest.mark.asyncio
async def test_orchestrator_skips_unimplemented_platform(
    db: AsyncSession, user: User
) -> None:
    """Contract test for the 'platform not implemented' path: regardless of
    which adapters are wired in factory.client_for, when one raises
    HTTPException(501) the orchestrator must classify the link as SKIPPED
    with error_code='platform_not_implemented'.

    Verified by patching `client_for` to raise — independent of any
    real-marketplace status. (Future TikTok/Temu/Aliexpress stubs land
    through this same code path.)
    """
    from fastapi import HTTPException

    _, store = await _make_company_store(db, user)
    integ = await _make_integration(db, user, store, IntegrationPlatform.SHOPEE)
    p, link = await _make_product_with_link(
        db, user, integ, store, bling_product_id=99, initial_stock=5
    )

    def _raise_501(*args, **kwargs):
        raise HTTPException(
            501,
            detail={"code": "platform_not_implemented", "platform": "stub"},
        )

    with patch("app.services.sync_orchestrator.client_for", side_effect=_raise_501):
        orch = SyncOrchestrator(db, user_id=user.id)
        report = await orch.run([p])

    await db.refresh(link)

    assert report.skipped == 1
    assert report.ok == 0
    assert link.last_sync_status == LinkSyncStatus.SKIPPED
    assert link.last_error is not None and "platform_not_implemented" in link.last_error

    logs = (
        await db.execute(select(SyncLog).where(SyncLog.product_link_id == link.id))
    ).scalars().all()
    assert len(logs) == 1
    assert logs[0].status == LinkSyncStatus.SKIPPED
    assert logs[0].error_code == "platform_not_implemented"


@pytest.mark.asyncio
async def test_orchestrator_persists_job_state(db: AsyncSession, user: User) -> None:
    _, store = await _make_company_store(db, user)
    integ = await _make_integration(db, user, store, IntegrationPlatform.BLING)
    p, _ = await _make_product_with_link(
        db, user, integ, store, bling_product_id=100, initial_stock=1
    )

    job = BackgroundJob(
        type=BackgroundJobType.SYNC_ALL,
        status=BackgroundJobStatus.PENDING,
        created_by=user.id,
        total=1,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    fake = _FakeBlingClient({100: 5})
    with patch("app.services.sync_orchestrator.client_for", return_value=fake):
        orch = SyncOrchestrator(db, user_id=user.id, job=job)
        await orch.run([p])

    await db.refresh(job)
    assert job.status == BackgroundJobStatus.SUCCEEDED
    assert job.processed == 1
    assert job.started_at is not None
    assert job.finished_at is not None
    assert job.result.get("ok") == 1


@pytest.mark.asyncio
async def test_advisory_lock_blocks_second_acquirer(
    db: AsyncSession, user: User
) -> None:
    """Two parallel sessions can't both acquire the per-user sync lock."""
    from app.db import SessionLocal

    async with SessionLocal() as s1, SessionLocal() as s2:
        async with try_user_sync_lock(s1, user.id) as got1:
            assert got1 is True
            async with try_user_sync_lock(s2, user.id) as got2:
                assert got2 is False
        # `pg_try_advisory_xact_lock` is TRANSACTION-scoped: exiting the
        # context manager releases nothing — the lock only drops when s1's
        # transaction ends. (This test predates the session→xact lock
        # migration; without the rollback, s1 still holds the lock here.)
        await s1.rollback()
        async with try_user_sync_lock(s2, user.id) as got3:
            assert got3 is True


@pytest.mark.asyncio
async def test_orchestrator_handles_missing_bling_stock(
    db: AsyncSession, user: User
) -> None:
    """If Bling returns a product without `estoque`, mark link as `skipped`."""
    _, store = await _make_company_store(db, user)
    integ = await _make_integration(db, user, store, IntegrationPlatform.BLING)
    p, link = await _make_product_with_link(
        db, user, integ, store, bling_product_id=55, initial_stock=3
    )

    fake = _FakeBlingClient({})  # get_product returns {} → parse → stock=None
    with patch("app.services.sync_orchestrator.client_for", return_value=fake):
        orch = SyncOrchestrator(db, user_id=user.id)
        report = await orch.run([p])

    await db.refresh(link)
    assert report.skipped == 1
    assert link.last_sync_status == LinkSyncStatus.SKIPPED
