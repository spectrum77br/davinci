"""Persistência do vínculo Magalu; executar apenas no banco local de testes."""

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BackgroundJob,
    BackgroundJobType,
    Integration,
    IntegrationPlatform,
    Listing,
    ListingStatus,
    Product,
    ProductLink,
)
from app.services import auto_link, listings_import


async def _integration(db, user):
    integration = Integration(
        user_id=user.id,
        platform=IntegrationPlatform.MAGALU,
        name=f"magalu-test-{uuid4().hex[:8]}",
        credentials=b"unused-by-fake-client",
    )
    db.add(integration)
    await db.commit()
    return integration


@pytest.mark.asyncio
async def test_auto_link_magalu_persists_kit_once_and_rejects_collision(
    db: AsyncSession, make_user,
):
    user = await make_user()
    integration = await _integration(db, user)
    kit = Product(user_id=user.id, sku="b099.20+a075", name="Kit", stock=12)
    colliding = [
        Product(user_id=user.id, sku="dup.1+a075", name="Kit duplicado", situacao="A"),
        Product(user_id=user.id, sku="dup.1.a075", name="Outro produto", situacao="E"),
    ]
    job = BackgroundJob(type=BackgroundJobType.AUTO_LINK, created_by=user.id)
    db.add_all([kit, *colliding, job])
    await db.commit()

    class Client:
        async def list_listings(self):
            for external in ("b099-20-a075", "dup-1-a075"):
                yield {
                    "external_id": external, "sku": external.replace("-", "."),
                    "status": "active", "raw": {"status": "PUBLISHED"},
                }

    first = await auto_link._link_via_listings(
        db, job.id, integration, Client(), IntegrationPlatform.MAGALU, repoint=True,
    )
    second = await auto_link._link_via_listings(
        db, job.id, integration, Client(), IntegrationPlatform.MAGALU, repoint=True,
    )
    db.expire_all()
    links = (await db.execute(select(ProductLink))).scalars().all()

    assert first["error"] is None and second["error"] is None
    assert first["created"] == 1 and first["sku_ambiguo"] == 1
    assert second["created"] == 0 and second["already_present"] == 1
    assert second["sku_ambiguo"] == 1
    assert len(links) == 1
    assert links[0].external_id == "b099-20-a075"
    assert links[0].external_sku == "b099.20+a075"
    assert links[0].variation_id is None
    assert links[0].product_id == (await db.execute(
        select(Product.id).where(Product.sku == "b099.20+a075")
    )).scalar_one()


@pytest.mark.asyncio
async def test_magalu_scan_keeps_five_explicit_replacements_and_existing_inactive_products(
    db: AsyncSession, make_user,
):
    user = await make_user()
    integration = await _integration(db, user)
    products = [
        Product(user_id=user.id, sku=sku, name=sku, situacao="A")
        for sku in ("a006", "a073", "a015", "a075", "a076")
    ]
    unchanged = [
        Product(user_id=user.id, sku="legacy.e", name="Excluded", situacao="E"),
        Product(user_id=user.id, sku="legacy.i", name="Inactive", situacao="I"),
    ]
    # The literal '-2' identity must not steal a mapping explicitly corrected
    # to the base product, even if a canonical '.2' product appears later.
    competing = Product(user_id=user.id, sku="a006.2", name="Distinct product")
    job = BackgroundJob(type=BackgroundJobType.AUTO_LINK, created_by=user.id)
    db.add_all([*products, *unchanged, competing, job])
    await db.flush()
    links = [
        ProductLink(
            user_id=user.id, product_id=product.id, integration_id=integration.id,
            platform=IntegrationPlatform.MAGALU,
            external_id=(
                product.sku + "-2" if product in products else product.sku.replace(".", "-")
            ),
            external_sku=product.sku, stock=7,
        )
        for product in [*products, *unchanged]
    ]
    db.add_all(links)
    await db.commit()
    identities = {link.id: (link.product_id, link.external_id, link.external_sku, link.stock)
                  for link in links}

    class Client:
        async def list_listings(self):
            for product in products:
                yield {
                    "external_id": product.sku, "sku": product.sku, "status": "paused",
                    "raw": {"status": "UNPUBLISHED"},
                }
                yield {
                    "external_id": product.sku + "-2", "sku": product.sku + ".2",
                    "status": "inactive" if product.sku == "a015" else "active",
                    "raw": {"status": "BLOCKED" if product.sku == "a015" else "PUBLISHED"},
                }
            for product in unchanged:
                yield {
                    "external_id": product.sku.replace(".", "-"),
                    "sku": product.sku, "status": "paused",
                    "raw": {"status": "UNPUBLISHED"},
                }

    for _ in range(2):
        result = await auto_link._link_via_listings(
            db, job.id, integration, Client(), IntegrationPlatform.MAGALU, repoint=True,
        )
        assert result["error"] is None
        assert result["created"] == result["repointed"] == 0
        assert result["not_published"] == 5
    db.expire_all()
    saved = (await db.execute(select(ProductLink))).scalars().all()
    assert {link.id: (link.product_id, link.external_id, link.external_sku, link.stock)
            for link in saved} == identities


@pytest.mark.asyncio
async def test_magalu_import_does_not_promote_old_skus_or_override_explicit_links(
    db: AsyncSession, make_user,
):
    user = await make_user()
    integration = await _integration(db, user)
    base = Product(user_id=user.id, sku="a006", name="Original product")
    competing = Product(user_id=user.id, sku="a006.2", name="Distinct product")
    blocked = Product(user_id=user.id, sku="blocked", name="Blocked product")
    db.add_all([base, competing, blocked])
    await db.flush()
    explicit = ProductLink(
        user_id=user.id, product_id=base.id, integration_id=integration.id,
        platform=IntegrationPlatform.MAGALU, external_id="a006-2", external_sku="a006",
    )
    rows = [
        Listing(
            user_id=user.id, integration_id=integration.id,
            platform=IntegrationPlatform.MAGALU, external_id=external,
            sku=sku, title=external, status=status, product_id=product_id,
            raw_data={"status": raw_status},
        )
        for external, sku, status, raw_status, product_id in [
            # A previously matched, freshly imported predecessor must not be
            # promoted again, even though it already carries product_id.
            ("a006", "a006", ListingStatus.PAUSED, "UNPUBLISHED", base.id),
            ("a006-2", "a006.2", ListingStatus.ACTIVE, "PUBLISHED", None),
            ("blocked", "blocked", ListingStatus.INACTIVE, "BLOCKED", None),
        ]
    ]
    db.add_all([explicit, *rows])
    await db.commit()
    base_id, explicit_id = base.id, explicit.id
    replacement_id, blocked_id = rows[1].id, rows[2].id
    assert await listings_import._link_by_sku(db) == 1
    assert await listings_import._create_product_links_for_matched(db) == 0
    assert await listings_import._link_by_sku(db) == 0
    assert await listings_import._create_product_links_for_matched(db) == 0
    await db.commit()
    db.expire_all()

    replacement = await db.get(Listing, replacement_id)
    assert replacement.product_id == base_id
    assert replacement.sku == "a006"
    assert (await db.get(Listing, blocked_id)).product_id is None
    saved = (await db.execute(select(ProductLink))).scalars().all()
    assert len(saved) == 1
    assert saved[0].id == explicit_id
    assert saved[0].product_id == base_id
    assert saved[0].external_id == "a006-2"


@pytest.mark.asyncio
async def test_import_magalu_flushes_kit_before_same_transaction_promotion(
    db: AsyncSession, make_user,
):
    user = await make_user()
    integration = await _integration(db, user)
    kit = Product(user_id=user.id, sku="b099.20+a075", name="Kit", stock=12)
    duplicate_products = [
        Product(user_id=user.id, sku="dup.1", name="Duplicado ativo", situacao="A"),
        Product(user_id=user.id, sku="dup.1", name="Duplicado excluído", situacao="E"),
    ]
    listing, ambiguous = [
        Listing(
            user_id=user.id, integration_id=integration.id,
            platform=IntegrationPlatform.MAGALU, external_id=external,
            sku=external.replace("-", "."), title=external,
            raw_data={"status": "PUBLISHED"},
        )
        for external in ("b099-20-a075", "dup-1")
    ]
    db.add_all([kit, *duplicate_products, listing, ambiguous])
    await db.commit()
    kit_id, listing_id, ambiguous_id = kit.id, listing.id, ambiguous.id

    matched = await listings_import._link_by_sku(db)
    # Não faça commit/flush aqui: a promoção textual precisa enxergar o
    # vínculo que o helper acabou de persistir na mesma transação.
    promoted = await listings_import._create_product_links_for_matched(db)
    await db.commit()
    db.expire_all()
    saved = await db.get(Listing, listing_id)
    rejected = await db.get(Listing, ambiguous_id)
    links = (await db.execute(select(ProductLink))).scalars().all()

    assert matched == 1
    assert promoted == 1
    assert saved.product_id == kit_id
    assert saved.sku == "b099.20+a075"
    assert rejected.product_id is None
    assert len(links) == 1
    assert links[0].product_id == kit_id
    assert links[0].external_id == "b099-20-a075"
    assert links[0].external_sku == "b099.20+a075"
    assert links[0].variation_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("already_matched", [False, True])
async def test_magalu_import_requires_publication_evidence_even_if_status_defaults_active(
    db: AsyncSession, make_user, already_matched: bool,
):
    user = await make_user()
    integration = await _integration(db, user)
    product = Product(user_id=user.id, sku="missing", name="Missing publication status")
    db.add(product)
    await db.flush()
    listing = Listing(
        user_id=user.id, integration_id=integration.id,
        platform=IntegrationPlatform.MAGALU, external_id="missing", sku="missing",
        title="Missing publication status", raw_data={},
        product_id=product.id if already_matched else None,
    )
    db.add(listing)
    await db.commit()
    assert listing.status == ListingStatus.ACTIVE
    assert await listings_import._link_by_sku(db) == 0
    assert await listings_import._create_product_links_for_matched(db) == 0
    assert (await db.execute(select(ProductLink))).scalars().all() == []
