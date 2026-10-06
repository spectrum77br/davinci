"""Push em lote de preços (Fase 9c) + relatório.

Os testes do Catálogo antigo (GET /catalog-listings, POST /push-catalog e a ⭐
`in_catalog`) saíram em 06/10/2026 junto com as rotas: o catálogo do ML virou
coluna de catálogo das contas de kit — ver test_pricing_catalogo_ml.py.
"""

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
    Integration,
    IntegrationPlatform,
    LinkSyncStatus,
    Marketplace,
    PricingAccount,
    PricingPlatform,
    PricingProduct,
    Product,
    ProductLink,
    Segment,
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
    """Uma integração ML, uma conta de kit (celular) e um produto com vínculo
    ao anúncio MLB1 (o envio em lote passa pelo push_one de verdade)."""
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
    raiz = Segment(name="Celular", slug="celular", sort_order=0)
    db.add(raiz)
    await db.flush()
    folha = Segment(name="Acessórios", slug="acessorios", sort_order=0, parent_id=raiz.id)
    db.add(folha)
    produto = Product(user_id=user_full.id, sku="REG-1", name="Reg")
    db.add(produto)
    await db.flush()
    db.add(
        ProductLink(
            user_id=user_full.id,
            product_id=produto.id,
            integration_id=integ.id,
            platform=IntegrationPlatform.ML,
            external_id="MLB1",
            last_sync_status=LinkSyncStatus.OK,
        )
    )

    p_reg = PricingProduct(
        user_id=user_full.id,
        sku="REG-1",
        name="Reg product",
        segment_id=folha.id,
        cost_kit1=Decimal("50.00"),
    )
    a_reg = PricingAccount(
        user_id=user_full.id,
        name="reg-acc",
        platform=PricingPlatform.ML,
        segment_id=raiz.id,
        kit_number=1,
        commission=Decimal("0.10"),
        margin1=Decimal("0.20"),
        shipping1=Decimal("5.00"),
        integration_id=integ.id,
    )
    db.add_all([p_reg, a_reg])
    await db.commit()
    for o in [p_reg, a_reg]:
        await db.refresh(o)
    return {
        "integration": integ,
        "p_reg": p_reg,
        "a_reg": a_reg,
    }


def _ml_item_ok(router) -> None:
    router.get(f"{ML_API_BASE}/items/MLB1").mock(
        return_value=Response(200, json={"id": "MLB1", "status": "active", "variations": []})
    )
    router.put(f"{ML_API_BASE}/items/MLB1").mock(
        return_value=Response(200, json={"id": "MLB1"})
    )


@pytest.mark.asyncio
async def test_rotas_do_catalogo_antigo_nao_existem_mais(
    client: AsyncClient,
    user_full: User,
    catalog_setup: dict[str, Any],
    auth_as: Callable[[User | None], None],
):
    auth_as(user_full)
    item = {
        "pricing_account_id": str(catalog_setup["a_reg"].id),
        "pricing_product_id": str(catalog_setup["p_reg"].id),
    }
    assert (await client.get("/api/pricing/catalog-listings")).status_code in (404, 405)
    r = await client.post("/api/pricing/push-catalog", json={"items": [item]})
    assert r.status_code in (404, 405)


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
    with respx.mock() as router:
        _ml_item_ok(router)
        await run_push_prices_batch(
            db,
            job_id=job.id,
            user_id=user_full.id,
            items=[
                {
                    "pricing_account_id": str(catalog_setup["a_reg"].id),
                    "pricing_product_id": str(catalog_setup["p_reg"].id),
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
    monkeypatch,
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

    # Token de mentira só durante ESTE teste: o monkeypatch devolve o ambiente
    # e o cache das settings é limpo de novo no fim — antes ele vazava para
    # os testes seguintes (test_telegram_client esperava token vazio).
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    from app.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]

    from app.services import telegram as tg_mod

    try:
        await _envia_lote_com_telegram(db, user_full, catalog_setup, job, tg_mod)
    finally:
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        get_settings.cache_clear()  # type: ignore[attr-defined]

    await db.refresh(job)
    assert job.status == BackgroundJobStatus.SUCCEEDED


async def _envia_lote_com_telegram(db, user_full, catalog_setup, job, tg_mod) -> None:
    with respx.mock() as router:
        _ml_item_ok(router)
        tg_route = router.post(
            f"{tg_mod.TELEGRAM_API_BASE}/botfake-token/sendMessage"
        ).mock(return_value=Response(200, json={"ok": True, "result": {}}))
        await run_push_prices_batch(
            db,
            job_id=job.id,
            user_id=user_full.id,
            items=[
                {
                    "pricing_account_id": str(catalog_setup["a_reg"].id),
                    "pricing_product_id": str(catalog_setup["p_reg"].id),
                }
            ],
            idempotency_prefix="batch-tg",
            notify_telegram=True,
        )
        assert tg_route.called


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
                    "pricing_account_id": str(catalog_setup["a_reg"].id),
                    "pricing_product_id": str(catalog_setup["p_reg"].id),
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
