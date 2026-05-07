"""Phase 13 — integration tests with marketplace fixtures.

Drives the full pipeline (SyncOrchestrator → factory → MercadoLivreClient)
against recorded ML responses via respx, then asserts the side effects:
DB sync_log row, metrics increment, and link state transition.
"""

from __future__ import annotations

import time
import uuid

import httpx
import pytest
import pytest_asyncio
import respx
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
    SyncLog,
    User,
    UserRole,
    UserStatus,
)
from app.security.cipher import encrypt_json
from app.services import metrics
from app.services.marketplaces.ml import ML_API_BASE
from app.services.sync_orchestrator import SyncOrchestrator


@pytest_asyncio.fixture
async def user(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:p13-{uuid.uuid4().hex[:8]}@davinci-test.com",
        email=f"p13-{uuid.uuid4().hex[:8]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions={},
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture(autouse=True)
async def _clean_metrics():
    await metrics.reset()
    yield
    await metrics.reset()


async def _setup_ml_link(
    db: AsyncSession, user: User, *, source_stock: int = 12
) -> tuple[Product, ProductLink]:
    company = Company(
        razao_social="Phase13 Co",
        cnpj=uuid.uuid4().hex[:14],
        apelido=f"p13co-{uuid.uuid4().hex[:6]}",
        responsavel_id=user.id,
    )
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
        user_id=user.id,
        store_id=store.id,
        platform=IntegrationPlatform.ML,
        name="ml-p13",
        credentials=encrypt_json(
            {
                "client_id": "x",
                "client_secret": "y",
                "access_token": "tok",
                "refresh_token": "ref",
                "user_id": 999,
                "expires_at": int(time.time()) + 3600,
            }
        ),
    )
    db.add(integ)
    await db.flush()
    product = Product(
        user_id=user.id,
        sku=f"sku-{uuid.uuid4().hex[:6]}",
        name="phase13 widget",
        stock=source_stock,
    )
    db.add(product)
    await db.flush()
    link = ProductLink(
        user_id=user.id,
        product_id=product.id,
        integration_id=integ.id,
        store_id=store.id,
        platform=IntegrationPlatform.ML,
        external_id="MLBX1",
        external_sku=product.sku,
        stock=0,
        last_sync_status=LinkSyncStatus.PENDING,
    )
    db.add(link)
    await db.commit()
    await db.refresh(product)
    await db.refresh(link)
    return product, link


@pytest.mark.asyncio
async def test_orchestrator_ml_full_loop_records_metrics(
    db: AsyncSession, user: User
) -> None:
    """Whole pipeline against respx fixtures: orchestrator dispatches to
    `MercadoLivreClient.update_stock`, ML responds 200, link is OK, sync_log
    row exists, and metrics counters reflect the run."""
    product, link = await _setup_ml_link(db, user, source_stock=15)

    item_payload = {"id": "MLBX1", "status": "active", "available_quantity": 0}

    with respx.mock(base_url=ML_API_BASE) as router:
        router.get("/items/MLBX1").mock(
            return_value=httpx.Response(200, json=item_payload)
        )
        put_route = router.put("/items/MLBX1").mock(
            return_value=httpx.Response(200, json={"id": "MLBX1"})
        )

        orch = SyncOrchestrator(db, user_id=user.id)
        report = await orch.run([product])

    assert put_route.called
    assert report.ok == 1
    assert report.fatal == 0

    await db.refresh(link)
    assert link.last_sync_status == LinkSyncStatus.OK
    assert link.last_error is None

    log = (
        await db.execute(select(SyncLog).where(SyncLog.product_link_id == link.id))
    ).scalar_one()
    assert log.status == LinkSyncStatus.OK
    assert log.platform == IntegrationPlatform.ML

    snap = await metrics.snapshot()
    ml = snap["platforms"]["ml"]
    assert ml["ok"] == 1
    assert ml["total"] == 1
    assert ml["samples"] == 1
    assert ml["avg_latency_ms"] >= 0


@pytest.mark.asyncio
async def test_orchestrator_records_fatal_with_error_code(
    db: AsyncSession, user: User
) -> None:
    """When ML returns 422, orchestrator marks FATAL and records the error
    code in the metrics histogram."""
    product, link = await _setup_ml_link(db, user, source_stock=8)

    with respx.mock(base_url=ML_API_BASE) as router:
        router.get("/items/MLBX1").mock(
            return_value=httpx.Response(
                200,
                json={"id": "MLBX1", "status": "active", "available_quantity": 0},
            )
        )
        router.put("/items/MLBX1").mock(
            return_value=httpx.Response(422, text='{"error":"invalid_param"}')
        )

        orch = SyncOrchestrator(db, user_id=user.id)
        await orch.run([product])

    await db.refresh(link)
    assert link.last_sync_status == LinkSyncStatus.FATAL

    snap = await metrics.snapshot()
    ml = snap["platforms"]["ml"]
    assert ml["fatal"] == 1
    codes = {e["code"] for e in ml["top_errors"]}
    assert any(c.startswith("ml_put_item_status_422") for c in codes)
