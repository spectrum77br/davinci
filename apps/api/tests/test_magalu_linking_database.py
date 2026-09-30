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
                yield {"external_id": external, "sku": external.replace("-", ".")}

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
