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


async def _make_ml_integration(db: AsyncSession, user: User) -> Integration:
    company = Company(razao_social="MLCO", apelido=f"mlco-{uuid.uuid4().hex[:6]}")
    db.add(company)
    await db.flush()
    store = Store(company_id=company.id, marketplace=Marketplace.ML, status=StoreStatus.ACTIVE)
    db.add(store)
    await db.flush()
    integ = Integration(
        user_id=user.id,
        store_id=store.id,
        platform=IntegrationPlatform.ML,
        name="dream",
        credentials=encrypt_json(
            {"access_token": "x", "user_id": 1, "expires_at": 9999999999}
        ),
    )
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    return integ


async def _make_tiktok_integration(db: AsyncSession, user: User) -> Integration:
    company = Company(razao_social="TTCO", apelido=f"tt-{uuid.uuid4().hex[:6]}")
    db.add(company)
    await db.flush()
    store = Store(company_id=company.id, marketplace=Marketplace.TIKTOK, status=StoreStatus.ACTIVE)
    db.add(store)
    await db.flush()
    integ = Integration(
        user_id=user.id,
        store_id=store.id,
        platform=IntegrationPlatform.TIKTOK,
        name="tt",
        credentials=encrypt_json(
            {"app_key": "k", "app_secret": "s", "access_token": "t", "shop_cipher": "c"}
        ),
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


# ------------------------------------------------- ML repoint on SKU change


@pytest.mark.asyncio
async def test_auto_link_ml_repoints_link_when_marketplace_sku_changes(
    db: AsyncSession, user: User, monkeypatch
):
    """Regressão do bug dos anúncios de catálogo (z0215): quando o vendedor
    troca o SELLER_SKU no ML, o anúncio (mesmo external_id) passa a resolver
    para OUTRO produto. O comportamento antigo via a chave (external_id,
    variation_id) já existente e pulava como 'already_present', deixando o
    link preso no produto velho (dg025.ra) com o estoque congelado. Agora o
    link é RE-APONTADO para o produto novo (z0215)."""
    integ = await _make_ml_integration(db, user)
    job = await _make_job(db, user)

    old = Product(user_id=user.id, sku="dg025.ra", name="Preto", stock=0, min_stock=0)
    new = Product(user_id=user.id, sku="z0215", name="Preto AVULSO", stock=6, min_stock=0)
    db.add_all([old, new])
    await db.flush()

    # Link legado: anúncio de catálogo apontando pro produto ERRADO (dg025.ra).
    stale = ProductLink(
        user_id=user.id,
        product_id=old.id,
        integration_id=integ.id,
        store_id=integ.store_id,
        platform=IntegrationPlatform.ML,
        external_id="MLB4638933507",
        variation_id=None,
        external_sku="dg025.ra",
        listing_title="Preto",
        stock=0,
        last_sync_status=LinkSyncStatus.SKIPPED,
    )
    db.add(stale)
    await db.commit()
    stale_id = stale.id

    from app.services.marketplaces import ml as ml_mod

    monkeypatch.setattr(
        ml_mod.MercadoLivreClient,
        "list_listings",
        _fake_listings_factory([
            # O anúncio agora carrega o SKU z0215 (o vendedor trocou no ML).
            {"external_id": "MLB4638933507", "variation_id": None, "sku": "z0215",
             "title": "Preto AVULSO", "listing_type": "gold_pro", "stock": 4},
        ]),
    )

    await run_auto_link(db, job_id=job.id, integration_ids=[integ.id])
    await db.refresh(job)

    assert job.status == BackgroundJobStatus.SUCCEEDED
    assert job.result["created"] == 0
    assert job.result["already_present"] == 0
    assert job.result["repointed"] == 1

    # A MESMA linha foi re-apontada (não duplicou): agora pro produto novo.
    links = (
        await db.execute(
            select(ProductLink).where(ProductLink.integration_id == integ.id)
        )
    ).scalars().all()
    assert len(links) == 1
    link = links[0]
    assert link.id == stale_id
    assert link.product_id == new.id
    assert link.external_sku == "z0215"
    assert link.stock == 4
    assert link.last_sync_status == LinkSyncStatus.OK


@pytest.mark.asyncio
async def test_auto_link_ml_keeps_link_already_present_when_sku_unchanged(
    db: AsyncSession, user: User, monkeypatch
):
    """Guard: se o SKU do anúncio NÃO mudou (resolve pro mesmo produto), o
    link continua contando como 'already_present' e não é reescrito nem
    contado como repoint."""
    integ = await _make_ml_integration(db, user)
    job = await _make_job(db, user)

    p = Product(user_id=user.id, sku="z0215", name="AVULSO", stock=6, min_stock=0)
    db.add(p)
    await db.flush()
    db.add(
        ProductLink(
            user_id=user.id,
            product_id=p.id,
            integration_id=integ.id,
            store_id=integ.store_id,
            platform=IntegrationPlatform.ML,
            external_id="MLB4638933507",
            variation_id=None,
            external_sku="z0215",
            last_sync_status=LinkSyncStatus.OK,
        )
    )
    await db.commit()

    from app.services.marketplaces import ml as ml_mod

    monkeypatch.setattr(
        ml_mod.MercadoLivreClient,
        "list_listings",
        _fake_listings_factory([
            {"external_id": "MLB4638933507", "variation_id": None, "sku": "z0215",
             "title": "AVULSO", "stock": 4},
        ]),
    )

    await run_auto_link(db, job_id=job.id, integration_ids=[integ.id])
    await db.refresh(job)

    assert job.result["created"] == 0
    assert job.result["repointed"] == 0
    assert job.result["already_present"] == 1


# ------------------------------------------------- Shopee/TikTok repoint


@pytest.mark.asyncio
async def test_auto_link_shopee_repoints_link_when_marketplace_sku_changes(
    db: AsyncSession, user: User, monkeypatch
):
    """Repoint agora vale p/ Shopee também: SKU do anúncio editado no painel
    (dg053.sp → dg053.ci) move o link pro produto novo em vez de pular."""
    integ = await _make_shopee_integration(db, user)
    job = await _make_job(db, user)

    old = Product(user_id=user.id, sku="dg053.sp", name="A", stock=0, min_stock=0)
    new = Product(user_id=user.id, sku="dg053.ci", name="B", stock=5, min_stock=0)
    db.add_all([old, new])
    await db.flush()
    stale = ProductLink(
        user_id=user.id,
        product_id=old.id,
        integration_id=integ.id,
        store_id=integ.store_id,
        platform=IntegrationPlatform.SHOPEE,
        external_id="900",
        variation_id="7",
        external_sku="dg053.sp",
        listing_title="A",
        stock=0,
        last_sync_status=LinkSyncStatus.SKIPPED,
    )
    db.add(stale)
    await db.commit()
    stale_id = stale.id

    from app.services.marketplaces import shopee as shopee_mod

    monkeypatch.setattr(
        shopee_mod.ShopeeClient,
        "list_listings",
        _fake_listings_factory([
            {"external_id": "900", "variation_id": "7", "sku": "dg053.ci",
             "title": "B", "stock": 5},
        ]),
    )

    await run_auto_link(db, job_id=job.id, integration_ids=[integ.id])
    await db.refresh(job)

    assert job.result["created"] == 0
    assert job.result["repointed"] == 1
    links = (
        await db.execute(
            select(ProductLink).where(ProductLink.integration_id == integ.id)
        )
    ).scalars().all()
    assert len(links) == 1
    assert links[0].id == stale_id
    assert links[0].product_id == new.id
    assert links[0].external_sku == "dg053.ci"


@pytest.mark.asyncio
async def test_auto_link_tiktok_repoints_link_when_seller_sku_changes(
    db: AsyncSession, user: User, monkeypatch
):
    """TikTok: sku_id interno estável, seller_sku editado (a003.sa → a003.ci)
    → re-aponta o link. Também cobre o dedup por (external_id, variation_id):
    antes a chave incluía product_id e o repoint viria como INSERT duplicado."""
    from app.services import auto_link as al
    from app.services.marketplaces import tiktok as tt_mod

    integ = await _make_tiktok_integration(db, user)
    job = await _make_job(db, user)

    old = Product(user_id=user.id, sku="a003.sa", name="Fone", stock=0, min_stock=0)
    new = Product(user_id=user.id, sku="a003.ci", name="Fone", stock=9, min_stock=0)
    db.add_all([old, new])
    await db.flush()
    stale = ProductLink(
        user_id=user.id,
        product_id=old.id,
        integration_id=integ.id,
        store_id=integ.store_id,
        platform=IntegrationPlatform.TIKTOK,
        external_id="TT1",
        variation_id="SK1",
        external_sku="a003.sa",
        listing_title="Fone",
        stock=0,
        last_sync_status=LinkSyncStatus.SKIPPED,
    )
    db.add(stale)
    await db.commit()

    async def _fake_search(self, **kw):  # noqa: ANN001
        return (
            [{"product_id": "TT1", "title": "Fone",
              "skus": [{"id": "SK1", "seller_sku": "a003.ci", "stock": 9}]}],
            None,
        )

    monkeypatch.setattr(tt_mod.TikTokClient, "search_products", _fake_search)

    stats = await al._link_tiktok_integration(db, job, integ)

    assert stats["created"] == 0
    assert stats["repointed"] == 1
    await db.refresh(stale)
    assert stale.product_id == new.id
    assert stale.external_sku == "a003.ci"
    links = (
        await db.execute(
            select(ProductLink).where(ProductLink.integration_id == integ.id)
        )
    ).scalars().all()
    assert len(links) == 1


# ---------------------------------------------------- TikTok page timeout (Item 1)


@pytest.mark.asyncio
async def test_link_tiktok_page_timeout_is_bounded(
    db: AsyncSession, user: User, monkeypatch
):
    """A wedged `search_products` must not hang the auto-link job: the per-page
    asyncio.wait_for wall trips, the account is marked failed with a timeout
    error, and control returns fast (the old code only checked the 2-min wall
    *between* pages, so a single hung call blocked TikTok linking forever)."""
    import asyncio
    import time

    from app.services import auto_link as al
    from app.services.marketplaces import tiktok as tt_mod

    job = await _make_job(db, user)
    # In-memory TikTok integration: the test schema's integration_platform enum
    # has no 'tiktok' value, but the timeout path creates no links, so nothing
    # is ever inserted that references it. Give it an explicit id (the model's
    # uuid default only fires on flush, which never happens here).
    integ = Integration(
        id=uuid.uuid4(),
        user_id=user.id,
        store_id=None,
        platform=IntegrationPlatform.TIKTOK,
        name="tt",
        credentials=encrypt_json(
            {"app_key": "k", "app_secret": "s", "access_token": "t", "shop_cipher": "c"}
        ),
    )

    async def _hang(self, **kwargs):  # noqa: ANN001
        await asyncio.sleep(30)  # would hang the job without the wait_for wall
        return [], None

    monkeypatch.setattr(tt_mod.TikTokClient, "search_products", _hang)
    monkeypatch.setattr(al, "TIKTOK_PAGE_TIMEOUT_SECONDS", 0.2)

    t0 = time.monotonic()
    stats = await al._link_tiktok_integration(db, job, integ)
    elapsed = time.monotonic() - t0

    assert elapsed < 5.0, f"took {elapsed:.1f}s — wait_for wall didn't trip"
    assert stats["created"] == 0
    assert stats["error"] and "timeout" in stats["error"]


def test_tiktok_client_has_explicit_timeout():
    """TikTokClient carries an explicit httpx connect+read timeout (not the bare
    15s float) so a slow endpoint can't stall a request indefinitely."""
    import httpx

    from app.services.marketplaces.tiktok import TIKTOK_DEFAULT_TIMEOUT, TikTokClient

    c = TikTokClient({"app_key": "k", "app_secret": "s"})
    assert isinstance(c._timeout, httpx.Timeout)
    assert isinstance(TIKTOK_DEFAULT_TIMEOUT, httpx.Timeout)
