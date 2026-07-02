"""auto_link — normalização de SKU, colisões e dedup de encoding legado."""

from __future__ import annotations

import uuid

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
    User,
    UserRole,
    UserStatus,
)
from app.security.cipher import encrypt_json
from app.services.auto_link import _norm_sku, _SkuIndex, run_auto_link


@pytest_asyncio.fixture
async def user(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:al-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"al-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _make_shopee_integration(db: AsyncSession, user: User) -> Integration:
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


async def _make_job(db: AsyncSession, user: User) -> BackgroundJob:
    job = BackgroundJob(
        type=BackgroundJobType.AUTO_LINK,
        status=BackgroundJobStatus.PENDING,
        created_by=user.id,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


# ------------------------------------------------------------------ unidade


def test_norm_sku_trim_lower_colapsa():
    assert _norm_sku("  ABC  123 ") == "abc 123"
    assert _norm_sku("a017.PI") == "a017.pi"
    assert _norm_sku(None) == ""
    # sufixos e acentos ficam — remoção silenciosa criaria match falso
    assert _norm_sku("café.kit2") == "café.kit2"


def test_sku_index_colisao_nao_sobrescreve():
    p1 = Product(user_id=uuid.uuid4(), sku="dup-1", name="a", stock=0, min_stock=0)
    p2 = Product(user_id=uuid.uuid4(), sku="DUP-1", name="b", stock=0, min_stock=0)
    p3 = Product(user_id=uuid.uuid4(), sku="only", name="c", stock=0, min_stock=0)
    idx = _SkuIndex([p1, p2, p3])

    got, motivo = idx.resolve("only")
    assert got is p3 and motivo == "match"

    got, motivo = idx.resolve("dup-1")
    assert got is None and motivo == "ambiguo"
    assert idx.ambiguous_count == 1
    assert idx.ambiguous_sample() == ["dup-1"]

    got, motivo = idx.resolve("nope")
    assert got is None and motivo == "not_found"


# ------------------------------------------------------------- run_auto_link


def _fake_listings_factory(items: list[dict]):
    async def _fake(self, **kw):
        for it in items:
            yield it
    return _fake


@pytest.mark.asyncio
async def test_auto_link_shopee_contadores_e_colisao(
    db: AsyncSession, user: User, monkeypatch
):
    integ = await _make_shopee_integration(db, user)
    job = await _make_job(db, user)

    ok = Product(user_id=user.id, sku="sku-ok", name="ok", stock=0, min_stock=0)
    dup_a = Product(user_id=user.id, sku="sku-dup", name="a", stock=0, min_stock=0)
    dup_b = Product(user_id=user.id, sku="SKU-DUP", name="b", stock=0, min_stock=0)
    db.add_all([ok, dup_a, dup_b])
    await db.commit()

    from app.services.marketplaces import shopee as shopee_mod

    monkeypatch.setattr(
        shopee_mod.ShopeeClient,
        "list_listings",
        _fake_listings_factory([
            # match normal — caixa/espaço divergentes dos dois lados
            {"external_id": "100", "variation_id": "1", "sku": " SKU-OK ",
             "title": "t", "stock": 3},
            # SKU colidido em products → não linka, conta como ambíguo
            {"external_id": "200", "variation_id": "2", "sku": "sku-dup", "title": "t", "stock": 1},
            # SKU vazio → contador próprio, não polui not_found
            {"external_id": "300", "variation_id": "3", "sku": "", "title": "t", "stock": 1},
            # SKU inexistente → not_found
            {"external_id": "400", "variation_id": "4", "sku": "ghost", "title": "t", "stock": 1},
        ]),
    )

    await run_auto_link(db, job_id=job.id, integration_ids=[integ.id])
    await db.refresh(job)

    assert job.status == BackgroundJobStatus.SUCCEEDED
    assert job.result["created"] == 1
    assert job.result["not_found"] == 1
    assert job.result["sku_vazio"] == 1
    assert job.result["sku_ambiguo"] == 1

    links = (
        await db.execute(
            select(ProductLink).where(ProductLink.integration_id == integ.id)
        )
    ).scalars().all()
    assert len(links) == 1
    assert links[0].product_id == ok.id
    assert links[0].external_id == "100"
    assert links[0].variation_id == "1"


@pytest.mark.asyncio
async def test_auto_link_shopee_nao_duplica_link_com_encoding_legado(
    db: AsyncSession, user: User, monkeypatch
):
    """Link legado (external_id="item_model", variation vazia) representa o
    mesmo anúncio que o canônico (item, model) — o auto_link não pode criar
    o segundo link (era o estoque 2x da Luminin)."""
    integ = await _make_shopee_integration(db, user)
    job = await _make_job(db, user)

    p = Product(user_id=user.id, sku="sku-leg", name="p", stock=0, min_stock=0)
    db.add(p)
    await db.flush()
    db.add(
        ProductLink(
            user_id=user.id,
            product_id=p.id,
            integration_id=integ.id,
            store_id=integ.store_id,
            platform=IntegrationPlatform.SHOPEE,
            external_id="123_456",
            variation_id=None,
            external_sku="sku-leg",
            last_sync_status=LinkSyncStatus.OK,
        )
    )
    await db.commit()

    from app.services.marketplaces import shopee as shopee_mod

    monkeypatch.setattr(
        shopee_mod.ShopeeClient,
        "list_listings",
        _fake_listings_factory([
            {"external_id": "123", "variation_id": "456", "sku": "sku-leg",
             "title": "t", "stock": 7},
        ]),
    )

    await run_auto_link(db, job_id=job.id, integration_ids=[integ.id])
    await db.refresh(job)

    assert job.result["created"] == 0
    assert job.result["already_present"] == 1
    links = (
        await db.execute(
            select(ProductLink).where(ProductLink.integration_id == integ.id)
        )
    ).scalars().all()
    assert len(links) == 1
