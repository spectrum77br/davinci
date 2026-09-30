"""Mercado Livre client + backfill regression tests (Fase 4b.ML).

Covers:
  B1 — never push qty=0 when source stock is positive
  B2 — backfill_ml_stock repopulates stock=0/last_sync_at=NULL links
  B3 — variation_id auto-remap by seller_sku when ML reshuffles variations

Tests use respx to stub the ML HTTP surface; no real network calls.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
import respx
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
from app.services.ml_backfill import run_backfill_ml_stock
from app.services.marketplaces.base import SyncStatus
from app.services.marketplaces.ml import (
    ML_API_BASE,
    MercadoLivreClient,
    _resolve_variation,
)


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
async def user(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:ml-{uuid.uuid4().hex[:8]}@davinci-test.com",
        email=f"ml-{uuid.uuid4().hex[:8]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions={},
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _make_setup(
    db: AsyncSession, user: User, *, link_stock: int = 0, link_status: LinkSyncStatus = LinkSyncStatus.PENDING
) -> tuple[Integration, Product, ProductLink]:
    company = Company(
        razao_social="ML Co",
        cnpj=uuid.uuid4().hex[:14],
        apelido=f"mlco-{uuid.uuid4().hex[:6]}",
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
        name="ml-test",
        credentials=encrypt_json(_ml_creds()),
    )
    db.add(integ)
    await db.flush()
    product = Product(
        user_id=user.id,
        sku=f"sku-{uuid.uuid4().hex[:6]}",
        name="ml widget",
        stock=10,
    )
    db.add(product)
    await db.flush()
    link = ProductLink(
        user_id=user.id,
        product_id=product.id,
        integration_id=integ.id,
        store_id=store.id,
        platform=IntegrationPlatform.ML,
        external_id="MLB123",
        external_sku=product.sku,
        stock=link_stock,
        last_sync_status=link_status,
    )
    db.add(link)
    await db.commit()
    await db.refresh(integ)
    await db.refresh(product)
    await db.refresh(link)
    return integ, product, link


# ---------------------------------------------------------------- B1 (zero block)


@pytest.mark.asyncio
@pytest.mark.parametrize("variations_field", [{}, {"variations": None}, {"variations": []}])
@pytest.mark.parametrize("remote_sku", ["dg091.pi", "dg090.pi"])
@pytest.mark.parametrize("qty", [0, 1])
async def test_removed_variation_never_updates_remaining_simple_item(
    variations_field: dict, remote_sku: str, qty: int,
) -> None:
    dead_since = datetime.now(UTC)
    product_id = uuid.uuid4()
    link = ProductLink(
        product_id=product_id, external_id="MLB123", variation_id="111",
        external_sku="dg091.pi", stock=77,
        morto_desde=dead_since, morto_motivo="variação removida",
    )
    client = MercadoLivreClient(_ml_creds())
    with respx.mock(base_url=ML_API_BASE, assert_all_called=False) as router:
        router.get("/items/MLB123").mock(return_value=httpx.Response(200, json={
            "id": "MLB123", "status": "paused", "sub_status": ["out_of_stock"],
            "attributes": [{"id": "SELLER_SKU", "value_name": remote_sku}],
            **variations_field,
        }))
        put = router.put("/items/MLB123").mock(return_value=httpx.Response(200, json={}))
        result = await client.update_stock(link, qty, force=True, sku_esperado="dg091.pi")

    assert not put.called
    assert result.status == SyncStatus.REQUIRES_REVIEW
    assert result.error_code == "ml_variation_not_found"
    assert result.qty_after is None
    assert "sku_atual" not in result.payload
    assert link.product_id == product_id
    assert link.variation_id == "111"
    assert link.external_sku == "dg091.pi"
    assert link.stock == 77
    assert link.morto_desde == dead_since


@pytest.mark.asyncio
async def test_simple_item_without_variation_id_still_updates_stock() -> None:
    link = ProductLink(external_id="MLB123", variation_id=None, stock=9)
    client = MercadoLivreClient(_ml_creds())
    with respx.mock(base_url=ML_API_BASE) as router:
        router.get("/items/MLB123").mock(return_value=httpx.Response(200, json={
            "id": "MLB123", "status": "paused", "sub_status": ["out_of_stock"],
            "variations": [],
            "attributes": [{"id": "SELLER_SKU", "value_name": "dg090.pi"}],
        }))
        put = router.put("/items/MLB123").mock(return_value=httpx.Response(200, json={}))
        result = await client.update_stock(link, 0, force=True, sku_esperado="dg090.pi")

    assert result.status == SyncStatus.OK
    assert result.qty_after == 0
    assert json.loads(put.calls.last.request.content) == {"available_quantity": 0}


@pytest.mark.asyncio
async def test_manual_sync_keeps_removed_variation_dead_and_on_original_product(
    db: AsyncSession, user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import sync_orchestrator

    _, product, link = await _make_setup(db, user, link_stock=77)
    product.sku = "dg091.pi"
    link.external_sku = product.sku
    link.variation_id = "111"
    link.morto_desde = datetime.now(UTC)
    link.morto_motivo = "variação removida"
    db.add(Product(user_id=user.id, sku="dg090.pi", name="surviving sibling", stock=0))
    await db.commit()
    dead_since = link.morto_desde
    original_product_id = product.id
    client = MercadoLivreClient(_ml_creds())
    monkeypatch.setattr(sync_orchestrator, "client_for", lambda *a, **kw: client)
    monkeypatch.setattr(
        sync_orchestrator.estoque_familia, "saldo_publicavel", AsyncMock(return_value=0),
    )
    monkeypatch.setattr(sync_orchestrator, "emit_alert", AsyncMock())

    with respx.mock(base_url=ML_API_BASE, assert_all_called=False) as router:
        router.get("/items/MLB123").mock(return_value=httpx.Response(200, json={
            "id": "MLB123", "status": "paused", "sub_status": ["out_of_stock"],
            "variations": [],
            "attributes": [{"id": "SELLER_SKU", "value_name": "dg090.pi"}],
        }))
        put = router.put("/items/MLB123").mock(return_value=httpx.Response(200, json={}))
        orch = sync_orchestrator.SyncOrchestrator(
            db, user_id=user.id, force=True, incluir_mortos=True,
        )
        report = await orch.run([product], only_link_ids=[link.id])

    await db.refresh(link)
    logs = (
        await db.execute(select(SyncLog).where(SyncLog.product_link_id == link.id))
    ).scalars().all()
    assert not put.called
    assert report.requires_review == 1
    assert report.ok == 0
    assert link.product_id == original_product_id
    assert link.variation_id == "111"
    assert link.external_sku == "dg091.pi"
    assert link.stock == 77
    assert link.morto_desde == dead_since
    assert link.morto_motivo == "variação removida"
    assert link.last_sync_status == LinkSyncStatus.REQUIRES_REVIEW
    assert len(logs) == 1
    assert logs[0].error_code == "ml_variation_not_found"
    assert logs[0].qty_after is None


@pytest.mark.asyncio
@pytest.mark.parametrize("qty", [0, 17])
@pytest.mark.parametrize("stored_id", ["222", "999"])
async def test_stock_update_preserves_all_variations(qty: int, stored_id: str) -> None:
    link = ProductLink(
        external_id="MLB123", variation_id=stored_id,
        external_sku="dg090.pi", stock=9,
    )
    client = MercadoLivreClient(_ml_creds())
    item = {
        "id": "MLB123", "status": "active",
        "variations": [
            {"id": 111, "available_quantity": 41},
            {"id": "222", "available_quantity": 9,
             "attributes": [{"id": "SELLER_SKU", "value_name": "dg090.pi"}]},
            {"id": 333, "available_quantity": 52},
        ],
    }
    updated_item = {
        **item,
        "variations": [
            {**v, "available_quantity": qty} if str(v["id"]) == "222" else v
            for v in item["variations"]
        ],
    }
    with respx.mock(base_url=ML_API_BASE) as router:
        router.get("/items/MLB123").mock(return_value=httpx.Response(200, json=item))
        put = router.put("/items/MLB123").mock(return_value=httpx.Response(200, json=updated_item))
        result = await client.update_stock(link, qty, force=True, sku_esperado="dg090.pi")

    assert result.status == SyncStatus.OK
    assert result.qty_after == qty
    assert json.loads(put.calls.last.request.content) == {
        "variations": [{"id": 111}, {"id": 222, "available_quantity": qty}, {"id": 333}]
    }
    assert link.variation_id == "222"


@pytest.mark.asyncio
@pytest.mark.parametrize("returned", [
    [{"id": 222, "available_quantity": 0}],
    [{"id": 111}, {"id": 222, "available_quantity": 8}, {"id": 333}],
    [{"id": 111}, {"id": 222}, {"id": 333}],
    [],
])
async def test_stock_update_does_not_confirm_changed_variations(returned: Any) -> None:
    link = ProductLink(external_id="MLB123", variation_id="222", stock=9)
    client = MercadoLivreClient(_ml_creds())
    with respx.mock(base_url=ML_API_BASE) as router:
        router.get("/items/MLB123").mock(return_value=httpx.Response(200, json={
            "id": "MLB123", "status": "active",
            "variations": [{"id": 111}, {"id": 222}, {"id": 333}],
        }))
        router.put("/items/MLB123").mock(return_value=httpx.Response(200, json={
            "id": "MLB123", "variations": returned,
        }))
        result = await client.update_stock(link, 0, force=True)

    assert result.status == SyncStatus.REQUIRES_REVIEW
    assert result.error_code == "ml_variations_changed"
    assert result.qty_after is None
    assert link.stock == 9


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_sibling", [
    None, {}, {"id": None}, {"id": 0}, {"id": -1}, {"id": True},
    {"id": 3.5}, {"id": "invalid"}, {"id": 222},
])
async def test_stock_update_refuses_incomplete_variation_ids(invalid_sibling: Any) -> None:
    link = ProductLink(external_id="MLB123", variation_id="222", stock=9)
    client = MercadoLivreClient(_ml_creds())
    item = {
        "id": "MLB123", "status": "active",
        "variations": [{"id": 111}, {"id": 222}, invalid_sibling],
    }
    with respx.mock(base_url=ML_API_BASE, assert_all_called=False) as router:
        router.get("/items/MLB123").mock(return_value=httpx.Response(200, json=item))
        put = router.put("/items/MLB123").mock(return_value=httpx.Response(200, json={}))
        result = await client.update_stock(link, 0, force=True)

    assert not put.called
    assert result.status == SyncStatus.REQUIRES_REVIEW
    assert result.error_code == "ml_variations_invalid"
    assert result.qty_after is None
    assert link.stock == 9


@pytest.mark.asyncio
async def test_stock_update_refuses_malformed_variations_array() -> None:
    link = ProductLink(external_id="MLB123", variation_id="222", stock=9)
    client = MercadoLivreClient(_ml_creds())
    with respx.mock(base_url=ML_API_BASE, assert_all_called=False) as router:
        router.get("/items/MLB123").mock(return_value=httpx.Response(
            200, json={"id": "MLB123", "status": "active", "variations": {"id": 222}}
        ))
        put = router.put("/items/MLB123").mock(return_value=httpx.Response(200, json={}))
        result = await client.update_stock(link, 0, force=True)

    assert not put.called
    assert result.error_code == "ml_variations_invalid"
    assert result.qty_after is None


@pytest.mark.asyncio
async def test_b1_never_pushes_zero_when_source_positive(
    db: AsyncSession, user: User
) -> None:
    """If caller asks qty=0 but the link's known stock is positive, return
    SKIPPED with `b1_guard_zero_block` and do NOT make any HTTP call to ML."""
    _, _, link = await _make_setup(db, user, link_stock=5)
    client = MercadoLivreClient(_ml_creds())

    with respx.mock(base_url=ML_API_BASE, assert_all_called=False) as router:
        # Any call would fail the test — guard must short-circuit.
        result = await client.update_stock(link, 0)
        assert len(router.calls) == 0

    assert result.status == SyncStatus.SKIPPED
    assert result.error_code == "b1_guard_zero_block"


@pytest.mark.asyncio
async def test_b1_pushes_zero_when_source_was_already_zero(
    db: AsyncSession, user: User
) -> None:
    """If qty=0 and link.stock=0, the guard does not fire (legitimate close-out)."""
    _, _, link = await _make_setup(db, user, link_stock=0)
    client = MercadoLivreClient(_ml_creds())

    with respx.mock(base_url=ML_API_BASE) as router:
        router.get("/items/MLB123").mock(
            return_value=httpx.Response(200, json={"id": "MLB123", "status": "active", "available_quantity": 0})
        )
        router.put("/items/MLB123").mock(return_value=httpx.Response(200, json={"id": "MLB123"}))
        result = await client.update_stock(link, 0)

    assert result.status == SyncStatus.OK
    assert result.qty_after == 0


# ------------------------------------------------------ listing-state guard


@pytest.mark.asyncio
async def test_force_still_skips_closed_listing(
    db: AsyncSession, user: User
) -> None:
    """A `closed`/`inactive`/`under_review` listing is locked by ML — a stock
    PUT always 400s (`field_not_updatable`). Even under force we must skip
    cleanly and NEVER fire the PUT (this was the vacation-mode error flood)."""
    _, _, link = await _make_setup(db, user, link_stock=0)
    client = MercadoLivreClient(_ml_creds())

    with respx.mock(base_url=ML_API_BASE, assert_all_called=False) as router:
        router.get("/items/MLB123").mock(
            return_value=httpx.Response(
                200, json={"id": "MLB123", "status": "closed"}
            )
        )
        put_route = router.put("/items/MLB123")
        result = await client.update_stock(link, 12, force=True)

    assert result.status == SyncStatus.SKIPPED
    assert result.error_code == "ml_listing_closed"
    assert not put_route.called


@pytest.mark.asyncio
async def test_force_pushes_through_paused_listing(
    db: AsyncSession, user: User
) -> None:
    """`paused` is reactivatable: pushing positive stock revives an out-of-stock
    pause. A forced sync must PUT through it (this is what re-stocking after
    vacation needs)."""
    _, _, link = await _make_setup(db, user, link_stock=0)
    client = MercadoLivreClient(_ml_creds())

    with respx.mock(base_url=ML_API_BASE) as router:
        router.get("/items/MLB123").mock(
            return_value=httpx.Response(
                200, json={"id": "MLB123", "status": "paused"}
            )
        )
        put_route = router.put("/items/MLB123").mock(
            return_value=httpx.Response(200, json={"id": "MLB123"})
        )
        result = await client.update_stock(link, 12, force=True)

    assert result.status == SyncStatus.OK
    assert result.qty_after == 12
    assert put_route.called


@pytest.mark.asyncio
async def test_auto_sync_skips_paused_listing(
    db: AsyncSession, user: User
) -> None:
    """Without force, a paused listing is still skipped (cron/automatic sync
    never reactivates on its own)."""
    _, _, link = await _make_setup(db, user, link_stock=0)
    client = MercadoLivreClient(_ml_creds())

    with respx.mock(base_url=ML_API_BASE, assert_all_called=False) as router:
        router.get("/items/MLB123").mock(
            return_value=httpx.Response(
                200, json={"id": "MLB123", "status": "paused"}
            )
        )
        put_route = router.put("/items/MLB123")
        result = await client.update_stock(link, 12, force=False)

    assert result.status == SyncStatus.SKIPPED
    assert result.error_code == "ml_listing_paused"
    assert not put_route.called


# ---------------------------------------------------------------- B3 (variation remap)


def test_resolve_variation_by_seller_sku_when_id_gone() -> None:
    """Stored variation_id no longer present → fall back to seller_sku match."""
    variations = [
        {
            "id": 999,
            "attributes": [{"id": "SELLER_SKU", "value_name": "ABC-001"}],
        },
        {
            "id": 888,
            "seller_custom_field": "abc-002",
        },
    ]
    v, repointed = _resolve_variation(variations, stored_var_id="111", seller_sku="ABC-001")
    assert v is not None and v["id"] == 999
    assert repointed is True


def test_resolve_variation_id_match_takes_priority() -> None:
    variations = [
        {"id": 111, "attributes": [{"id": "SELLER_SKU", "value_name": "X"}]},
        {"id": 222, "attributes": [{"id": "SELLER_SKU", "value_name": "Y"}]},
    ]
    v, repointed = _resolve_variation(variations, stored_var_id="111", seller_sku="Y")
    assert v["id"] == 111
    assert repointed is False


def test_resolve_variation_returns_none_when_no_match() -> None:
    variations = [{"id": 111, "attributes": [{"id": "SELLER_SKU", "value_name": "X"}]}]
    v, repointed = _resolve_variation(variations, stored_var_id="999", seller_sku="Z")
    assert v is None
    assert repointed is False


@pytest.mark.asyncio
async def test_b3_remaps_variation_and_writes(db: AsyncSession, user: User) -> None:
    """End-to-end: stored variation_id is gone; client finds new id via
    seller_sku and PUTs against the new id."""
    _, _, link = await _make_setup(db, user, link_stock=0)
    link.variation_id = "111"
    db.add(link)
    await db.commit()

    client = MercadoLivreClient(_ml_creds())

    item_payload = {
        "id": "MLB123",
        "status": "active",
        "variations": [
            {
                "id": 999,
                "attributes": [{"id": "SELLER_SKU", "value_name": link.external_sku}],
                "available_quantity": 7,
            }
        ],
    }

    with respx.mock(base_url=ML_API_BASE) as router:
        router.get("/items/MLB123").mock(return_value=httpx.Response(200, json=item_payload))
        put_route = router.put("/items/MLB123").mock(
            return_value=httpx.Response(200, json={"id": "MLB123"})
        )
        result = await client.update_stock(link, 12)

    # Persist the variation_id mutation the client applied to the dirty link
    # so the autouse cleanup teardown doesn't race with a flush of stale state.
    await db.commit()

    assert result.status == SyncStatus.OK
    assert result.qty_after == 12
    assert put_route.called
    body = put_route.calls.last.request.content
    assert b'"available_quantity": 12' in body or b'"available_quantity":12' in body
    assert b'999' in body  # the new variation id was used


@pytest.mark.asyncio
async def test_b3_marks_review_when_no_variation_match(
    db: AsyncSession, user: User
) -> None:
    """variation_id gone AND no SKU match → REQUIRES_REVIEW (not FATAL — needs
    human eye, not retry)."""
    _, _, link = await _make_setup(db, user, link_stock=0)
    link.variation_id = "111"
    db.add(link)
    await db.commit()

    client = MercadoLivreClient(_ml_creds())
    item_payload = {
        "id": "MLB123",
        "status": "active",
        "variations": [
            {"id": 999, "attributes": [{"id": "SELLER_SKU", "value_name": "OTHER-SKU"}]}
        ],
    }
    with respx.mock(base_url=ML_API_BASE) as router:
        router.get("/items/MLB123").mock(return_value=httpx.Response(200, json=item_payload))
        result = await client.update_stock(link, 5)

    assert result.status == SyncStatus.REQUIRES_REVIEW
    assert result.error_code == "ml_variation_not_found"


# ---------------------------------------------------------------- B2 (backfill)


@pytest.mark.asyncio
async def test_b2_backfill_repopulates_zero_links(
    db: AsyncSession, user: User
) -> None:
    """Link with stock=0/last_sync_at=NULL gets stock pulled from ML and a
    SyncLog row appended."""
    integ, _, link = await _make_setup(
        db, user, link_stock=0, link_status=LinkSyncStatus.PENDING
    )
    job = BackgroundJob(
        type=BackgroundJobType.BACKFILL_ML_STOCK,
        status=BackgroundJobStatus.PENDING,
        created_by=user.id,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    with respx.mock(base_url=ML_API_BASE) as router:
        router.get("/items/MLB123").mock(
            return_value=httpx.Response(
                200, json={"id": "MLB123", "status": "active", "available_quantity": 21}
            )
        )
        result = await run_backfill_ml_stock(db, job_id=job.id, user_id=user.id)

    assert result == {"scanned": 1, "repaired": 1, "skipped": 0, "errored": 0}
    await db.refresh(link)
    assert link.stock == 21
    assert link.last_sync_at is not None
    assert link.last_sync_status == LinkSyncStatus.OK

    logs = (
        await db.execute(select(SyncLog).where(SyncLog.job_id == job.id))
    ).scalars().all()
    assert len(logs) == 1
    assert logs[0].qty_before == 0
    assert logs[0].qty_after == 21
    assert logs[0].payload.get("source") == "ml_backfill"

    await db.refresh(job)
    assert job.status == BackgroundJobStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_b2_backfill_uses_variation_qty_when_present(
    db: AsyncSession, user: User
) -> None:
    """If link has variation_id, backfill reads `available_quantity` from that
    specific variation, not the parent."""
    _, _, link = await _make_setup(db, user, link_stock=0)
    link.variation_id = "555"
    db.add(link)
    await db.commit()

    job = BackgroundJob(
        type=BackgroundJobType.BACKFILL_ML_STOCK,
        status=BackgroundJobStatus.PENDING,
        created_by=user.id,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    with respx.mock(base_url=ML_API_BASE) as router:
        router.get("/items/MLB123").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "MLB123",
                    "status": "active",
                    "available_quantity": 99,  # noise: parent total
                    "variations": [
                        {"id": 555, "available_quantity": 7},
                        {"id": 666, "available_quantity": 3},
                    ],
                },
            )
        )
        result = await run_backfill_ml_stock(db, job_id=job.id, user_id=user.id)

    assert result["repaired"] == 1
    await db.refresh(link)
    assert link.stock == 7
