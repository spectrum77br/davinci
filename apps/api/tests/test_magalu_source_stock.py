"""Source confirmation for Magalu/ML sellouts; no database or network required."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models import IntegrationPlatform, Product, ProductLink
from app.services import sync_orchestrator as module
from app.services.marketplaces.base import SyncResult, SyncStatus
from app.services.marketplaces.bling import BlingClient


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class _Session:
    def __init__(self, harness):
        self.harness = harness
        self.added = []
        self.commit = AsyncMock()

    async def get(self, model, row_id):
        rows = self.harness.products if model is Product else self.harness.integrations
        return rows.get(row_id)

    async def execute(self, statement):
        params = statement.compile().params
        product_id = params.get("product_id_1")
        if not isinstance(product_id, list) and product_id in self.harness.products:
            links = [link for link in self.harness.links if link.product_id == product_id]
            if "id_1" in params:
                links = [link for link in links if link.id in params["id_1"]]
            return _Rows(links)
        return _Rows([])

    def add(self, row):
        self.added.append(row)
        self.harness.logs.append(row)


class _Bling(BlingClient):
    def __init__(self, harness):
        self.harness = harness

    async def get_product_stock_smart(self, external_id, *, sku):
        value = self.harness.source_stock[external_id]
        if isinstance(value, Exception):
            raise value
        # Exercise the actual _refresh_bling, including missing/zero/negative
        # stock and local product updates, without contacting Bling.
        return {"stock": value, "raw": {}, "found_via": "direct"}


class _Marketplace:
    def __init__(self):
        self.calls = []

    async def update_stock(self, link, qty, *, force, **kwargs):
        self.calls.append((link.id, qty, force))
        if qty == 0 and (link.stock or 0) > 0 and not force:
            return SyncResult(status=SyncStatus.SKIPPED, error_code="b1_guard_zero_block")
        return SyncResult(status=SyncStatus.OK, qty_after=qty)


class _Harness:
    def __init__(self, monkeypatch, platform):
        self.platform = platform
        self.user_id = uuid4()
        self.products = {}
        self.links = []
        self.integrations = {}
        self.source_stock = {}
        self.logs = []
        self.published_qty = {}
        self.bling = _Bling(self)
        self.marketplace = _Marketplace()
        self.session = _Session(self)

        async def client(_orch, integration):
            if integration.platform == IntegrationPlatform.BLING:
                return self.bling
            return self.marketplace

        async def saldo(_session, product, *, cache):
            return self.published_qty.get(product.id, max(0, int(product.stock or 0)))

        @asynccontextmanager
        async def session_scope():
            yield _Session(self)

        monkeypatch.setattr(module.SyncOrchestrator, "_client", client)
        monkeypatch.setattr(module.SyncOrchestrator, "_emit_link_alerts", AsyncMock())
        monkeypatch.setattr(module.estoque_familia, "saldo_publicavel", saldo)
        monkeypatch.setattr(module, "session_scope", session_scope)

    def add_link(self, product, platform, *, source_stock=None):
        integration = SimpleNamespace(
            id=uuid4(), platform=platform, archived_at=None, vacation_mode=False
        )
        self.integrations[integration.id] = integration
        external_id = str(len(self.links) + 1)
        link = ProductLink(
            id=uuid4(), product_id=product.id, user_id=self.user_id,
            integration_id=integration.id, platform=platform,
            external_id=external_id, stock=105,
        )
        self.links.append(link)
        if platform == IntegrationPlatform.BLING:
            self.source_stock[int(external_id)] = source_stock
        return link

    def product(self, *, source_stock=0, with_bling=True, platform=None):
        product = Product(
            id=uuid4(), user_id=self.user_id, sku=f"b039.{len(self.products)}", stock=0
        )
        self.products[product.id] = product
        if with_bling:
            self.add_link(product, IntegrationPlatform.BLING, source_stock=source_stock)
        target = self.add_link(product, platform or self.platform)
        return product, target

    async def run(self, mode, *, force=False, orch=None, only_link_ids=None):
        orch = orch or module.SyncOrchestrator(self.session, user_id=self.user_id, force=force)
        if mode == "parallel":
            await orch.run_parallel(list(self.products), only_link_ids=only_link_ids)
        else:
            await orch.run(list(self.products.values()), only_link_ids=only_link_ids)
        return orch


@pytest.fixture(params=[IntegrationPlatform.MAGALU, IntegrationPlatform.ML])
def h(monkeypatch, request):
    return _Harness(monkeypatch, request.param)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sequential", "parallel"])
@pytest.mark.parametrize("source_stock", [0, -3])
async def test_confirmed_bling_sellout_reaches_marketplace(h, mode, source_stock):
    product, link = h.product(source_stock=source_stock)
    await h.run(mode)
    assert h.marketplace.calls == [(link.id, 0, True)]
    assert link.stock == product.stock == 0
    assert link.last_sync_status.value == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sequential", "parallel"])
@pytest.mark.parametrize("source_stock", [None, RuntimeError("Bling unavailable")])
@pytest.mark.parametrize("force", [False, True])
async def test_attempted_bling_without_stock_never_pushes_marketplace(h, mode, source_stock, force):
    product, link = h.product(source_stock=source_stock)
    product.stock = 17  # An old positive cache is not source confirmation either.
    await h.run(mode, force=force)
    assert h.marketplace.calls == []
    assert link.stock == 105
    assert link.last_sync_status.value == "skipped"
    assert link.last_error.startswith("bling_refresh_failed_no_push:")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sequential", "parallel"])
async def test_positive_stock_does_not_force_bypass(h, mode):
    _, link = h.product(source_stock=17)
    await h.run(mode)
    assert h.marketplace.calls == [(link.id, 17, False)]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sequential", "parallel"])
async def test_family_quantity_is_preserved_when_own_stock_is_zero(h, mode):
    product, link = h.product(source_stock=0)
    h.published_qty[product.id] = 52
    await h.run(mode)
    assert h.marketplace.calls == [(link.id, 52, False)]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sequential", "parallel"])
@pytest.mark.parametrize("force", [False, True])
async def test_marketplace_only_scope_preserves_explicit_force_behavior(h, mode, force):
    _, link = h.product(source_stock=0)
    await h.run(mode, force=force, only_link_ids=[link.id])
    assert h.marketplace.calls == [(link.id, 0, force)]
    assert link.last_sync_status.value == ("ok" if force else "skipped")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sequential", "parallel"])
async def test_confirmation_cannot_leak_between_products(h, mode):
    _, confirmed = h.product(source_stock=0)
    _, unconfirmed = h.product(with_bling=False)
    await h.run(mode)
    assert (confirmed.id, 0, True) in h.marketplace.calls
    assert (unconfirmed.id, 0, False) in h.marketplace.calls
    assert unconfirmed.last_sync_status.value == "skipped"


@pytest.mark.asyncio
@pytest.mark.parametrize("h", [IntegrationPlatform.ML], indirect=True)
@pytest.mark.parametrize("mode", ["sequential", "parallel"])
async def test_ml_source_confirmation_does_not_force_a_repointed_sku(h, monkeypatch, mode):
    product, link = h.product(source_stock=0)
    replacement = Product(id=uuid4(), user_id=h.user_id, sku="replacement", stock=0)
    monkeypatch.setattr(
        module.SyncOrchestrator, "_produto_do_sku", AsyncMock(return_value=replacement)
    )
    update_stock = h.marketplace.update_stock

    async def sku_changed(link, qty, *, force, **kwargs):
        if kwargs.get("sku_esperado") == product.sku:
            h.marketplace.calls.append((link.id, qty, force))
            return SyncResult(
                status=SyncStatus.REQUIRES_REVIEW,
                error_code="sku_trocado",
                payload={"sku_atual": replacement.sku},
            )
        return await update_stock(link, qty, force=force, **kwargs)

    monkeypatch.setattr(h.marketplace, "update_stock", sku_changed)
    await h.run(mode)

    assert h.marketplace.calls == [(link.id, 0, True), (link.id, 0, False)]
    assert link.product_id == replacement.id
    assert link.stock == 105
    assert link.last_sync_status.value == "skipped"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sequential", "parallel"])
async def test_confirmation_cannot_survive_a_failed_second_pass(h, mode):
    _, link = h.product(source_stock=0)
    orch = await h.run(mode)
    link.stock = 105
    h.marketplace.calls.clear()
    h.source_stock = dict.fromkeys(h.source_stock, None)
    await h.run(mode, orch=orch)
    assert h.marketplace.calls == []
    assert link.stock == 105


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sequential", "parallel"])
@pytest.mark.parametrize("sources", [(None, 0), (0, None)])
async def test_any_missing_bling_source_blocks_marketplace(h, mode, sources):
    product, link = h.product(source_stock=sources[0])
    h.add_link(product, IntegrationPlatform.BLING, source_stock=sources[1])
    await h.run(mode, force=True)
    assert h.marketplace.calls == []
    assert link.stock == 105


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sequential", "parallel"])
@pytest.mark.parametrize("force", [False, True])
async def test_cached_bling_refresh_does_not_count_as_confirmation(h, monkeypatch, mode, force):
    _, link = h.product(source_stock=0)
    h.links[0].last_sync_at = datetime.now(UTC)
    monkeypatch.setattr(module, "BLING_REFRESH_TTL_SECONDS", 3600)
    await h.run(mode, force=force)
    assert h.marketplace.calls == []
    assert link.stock == 105
    assert link.last_error.startswith("bling_refresh_failed_no_push:")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sequential", "parallel"])
@pytest.mark.parametrize("source_stock", [0, None])
@pytest.mark.parametrize(
    "platform", [IntegrationPlatform.SHOPEE, IntegrationPlatform.TIKTOK, IntegrationPlatform.AMAZON]
)
async def test_other_platforms_keep_existing_guard_behavior(h, mode, source_stock, platform):
    _, link = h.product(source_stock=source_stock, platform=platform)
    await h.run(mode)
    assert h.marketplace.calls == [(link.id, 0, False)]
    assert link.last_sync_status.value == "skipped"


@pytest.mark.parametrize("result", [
    SyncResult(status=SyncStatus.SKIPPED, qty_after=0, payload={"source": "bling_refresh_cached"}),
    SyncResult(status=SyncStatus.OK, qty_after=None, payload={"source": "bling_refresh"}),
    SyncResult(status=SyncStatus.OK, qty_after=0),
    SyncResult(status=SyncStatus.RETRYABLE, qty_after=0, payload={"source": "bling_refresh"}),
])
def test_status_or_cached_quantity_alone_is_not_confirmation(result):
    assert not module._confirmed_bling_stock(result)
