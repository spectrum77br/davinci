"""Magalu kit matching without database or marketplace I/O (--noconftest)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.models import IntegrationPlatform, Listing, Product, ProductLink
from app.services import auto_link, listings_import


def _product(sku, *, situacao="A"):
    return Product(id=uuid4(), user_id=uuid4(), sku=sku, name=sku, situacao=situacao)


def _rows(items):
    result = Mock()
    result.scalars.return_value.all.return_value = items
    return result


class _Client:
    def __init__(self, items):
        self.items = items

    async def list_listings(self):
        for item in self.items:
            yield item


async def _run_adapter(monkeypatch, products, items, existing=(), platform=None):
    platform = platform or IntegrationPlatform.MAGALU
    session = SimpleNamespace(
        execute=AsyncMock(side_effect=[_rows(products), _rows(list(existing))]),
        commit=AsyncMock(),
    )
    integration = SimpleNamespace(
        id=uuid4(), user_id=uuid4(), store_id=uuid4(), platform=platform,
    )
    created = []

    async def capture_flush(_session, pending):
        created.extend(pending)
        return len(pending), 0

    monkeypatch.setattr(auto_link, "_safe_flush_batch", capture_flush)
    monkeypatch.setattr(auto_link, "_heartbeat", AsyncMock())
    stats = await auto_link._link_via_listings(
        session, uuid4(), integration, _Client(items), platform, repoint=True,
    )
    assert stats["error"] is None
    session.commit.assert_awaited_once()
    return stats, created


@pytest.mark.parametrize(
    ("canonical", "external"),
    [
        ("b009.8.12.20", "b009-8-12-20"),
        ("b099.20+a075+bp003+a076", "b099-20-a075-bp003-a076"),
        (" B099.20+A075 ", " b099-20-a075 "),
    ],
)
def test_magalu_index_resolves_encoded_catalog(canonical, external):
    product = _product(canonical)
    index = auto_link._SkuIndex([product], normalize=auto_link._magalu_sku_key)

    assert index.resolve(external) == (product, "match")


@pytest.mark.parametrize(
    "skus",
    [
        ["b099.20+a075", "b099.20.a075"],
        ["b099.20+a075", "b099-20-a075"],
        ["b099.20+a075", "B099.20+A075"],
    ],
)
def test_magalu_index_rejects_every_encoded_collision(skus):
    # An inactive/deleted candidate still makes the identity ambiguous.
    products = [_product(skus[0]), _product(skus[1], situacao="E")]
    index = auto_link._SkuIndex(products, normalize=auto_link._magalu_sku_key)

    assert index.resolve("b099-20-a075") == (None, "ambiguo")
    assert index.ambiguous_count == 1
    assert index.ambiguous_sample() == ["b099-20-a075"]


def test_default_index_preserves_distinct_separators():
    products = [_product(sku) for sku in ("b099.20+a075", "b099.20.a075", "b099-20-a075")]
    index = auto_link._SkuIndex(products)

    for product in products:
        assert index.resolve(product.sku.upper()) == (product, "match")


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_state", ["missing", "correct", "wrong"])
async def test_magalu_adapter_creates_or_repairs_kit_link(monkeypatch, existing_state):
    product = _product("b099.20+a075+bp003+a076")
    external = "b099-20-a075-bp003-a076"
    existing = []
    if existing_state != "missing":
        existing.append(ProductLink(
            id=uuid4(),
            product_id=product.id if existing_state == "correct" else uuid4(),
            external_id=external,
            external_sku=external.replace("-", "."),
            platform=IntegrationPlatform.MAGALU,
        ))
    stats, created = await _run_adapter(
        monkeypatch, [product],
        [{"external_id": external, "sku": external.replace("-", "."), "status": "active"}],
        existing,
    )

    link = created[0] if created else existing[0]
    assert link.product_id == product.id
    assert link.external_id == external
    assert link.external_sku == product.sku
    assert link.variation_id is None
    assert stats["created"] == (existing_state == "missing")
    assert stats["already_present"] == (existing_state == "correct")
    assert stats["repointed"] == (existing_state == "wrong")


@pytest.mark.asyncio
async def test_magalu_adapter_uses_raw_identity_even_without_normalized_sku(monkeypatch):
    product = _product("b009.8")
    stats, created = await _run_adapter(
        monkeypatch, [product], [{"external_id": "b009-8", "sku": None}],
    )

    assert stats["created"] == 1
    assert stats["sku_vazio"] == 0
    assert created[0].external_sku == "b009.8"


@pytest.mark.asyncio
@pytest.mark.parametrize("has_existing", [False, True])
async def test_magalu_adapter_does_not_link_or_repoint_collisions(monkeypatch, has_existing):
    products = [_product("b099.20+a075"), _product("b099.20.a075", situacao="E")]
    original_id = products[1].id
    original_sku = products[1].sku
    existing = [ProductLink(
        id=uuid4(), product_id=original_id, external_id="b099-20-a075",
        external_sku=original_sku, platform=IntegrationPlatform.MAGALU,
    )] if has_existing else []
    stats, created = await _run_adapter(
        monkeypatch, products,
        [{"external_id": "b099-20-a075", "sku": "b099.20.a075"}], existing,
    )

    assert not created
    assert stats["sku_ambiguo"] == 1
    assert stats["repointed"] == 0
    if existing:
        assert existing[0].product_id == original_id
        assert existing[0].external_sku == original_sku


@pytest.mark.asyncio
async def test_magalu_adapter_does_not_guess_seller_suffix(monkeypatch):
    stats, created = await _run_adapter(
        monkeypatch, [_product("a076")], [{"external_id": "a076-2", "sku": "a076.2"}],
    )

    assert not created
    assert stats["not_found"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", [IntegrationPlatform.ML, IntegrationPlatform.SHOPEE])
async def test_other_adapters_keep_matching_seller_sku(monkeypatch, platform):
    product = _product("b099.20+a075")
    distinct = _product("b099.20.a075")
    stats, created = await _run_adapter(
        monkeypatch, [product, distinct],
        [{"external_id": "123", "sku": product.sku.upper(), "variation_id": "456"}],
        platform=platform,
    )

    assert stats["created"] == 1
    assert created[0].product_id == product.id
    assert created[0].external_id == "123"
    assert created[0].variation_id == "456"


@pytest.mark.asyncio
async def test_listings_import_uses_same_unique_encoder_and_preserves_kit_sku():
    kit = _product("b099.20+a075")
    simple = _product("b009.8")
    products = [kit, simple, _product("dup.1"), _product("dup+1", situacao="I")]
    listings = [
        Listing(external_id=external, sku=external.replace("-", "."))
        for external in ("b099-20-a075", "b009-8", "dup-1", "a076-2")
    ]
    session = SimpleNamespace(
        execute=AsyncMock(side_effect=[
            SimpleNamespace(rowcount=3), _rows(listings), _rows(products),
        ]),
        flush=AsyncMock(),
    )

    linked = await listings_import._link_by_sku(session)

    assert linked == 5  # Three unchanged non-Magalu matches plus two Magalu matches.
    assert [(row.product_id, row.sku) for row in listings[:2]] == [
        (kit.id, kit.sku), (simple.id, simple.sku),
    ]
    assert all(row.product_id is None for row in listings[2:])
    session.flush.assert_awaited_once()
    generic_update = session.execute.await_args_list[0].args[0]
    sql = str(generic_update.compile(compile_kwargs={"literal_binds": True}))
    assert "platform != 'magalu'" in sql


@pytest.mark.asyncio
async def test_listings_import_does_not_load_products_without_unlinked_magalu():
    session = SimpleNamespace(execute=AsyncMock(return_value=_rows([])), flush=AsyncMock())

    assert await listings_import._link_magalu_by_sku(session) == 0
    session.execute.assert_awaited_once()
    session.flush.assert_not_awaited()
