"""Durable outbox integration tests; disposable LOCAL PostgreSQL, mocked HTTP.

Run with --noconftest and CARREFOUR_TEST_DATABASE_URL. UUID schemas are removed.
"""

# ruff: noqa: S608
import asyncio
import copy
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import db
from app.models import Integration, IntegrationPlatform, Listing, ProductLink, SyncLog
from app.models.carrefour_offer_write import CarrefourOfferBatch, CarrefourOfferWrite
from app.models.enums import LinkSyncStatus, ListingStatus, SyncLogAction
from app.security.cipher import decrypt_json, encrypt_json
from app.services import carrefour_offer_write as queue
from app.services.marketplaces import carrefour
from app.services.marketplaces.base import SyncStatus

TEST_DATABASE = os.environ.get("CARREFOUR_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.skipif(not TEST_DATABASE, reason="requires disposable local PostgreSQL"),
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest_asyncio.fixture(loop_scope="session")
async def env(monkeypatch):
    url = make_url(TEST_DATABASE)
    assert url.host in {"127.0.0.1", "localhost", "::1"}
    assert url.database == "carrefour_write_20261117"
    schema = "carrefour_outbox_test_" + uuid4().hex
    engine = create_async_engine(
        url,
        poolclass=NullPool,
        execution_options={"schema_translate_map": {Integration.__table__.schema: schema}},
        connect_args={"server_settings": {"search_path": schema + ",public"}},
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        for name, enum in (
            ("integration_platform", IntegrationPlatform),
            ("link_sync_status", LinkSyncStatus),
            ("listing_status", ListingStatus),
            ("sync_log_action", SyncLogAction),
        ):
            values = ",".join("'" + e.value + "'" for e in enum)
            await connection.execute(text(f'CREATE TYPE "{schema}".{name} AS ENUM ({values})'))
        for table in ("users", "stores", "background_jobs"):
            await connection.execute(text(f'CREATE TABLE "{schema}".{table} (id uuid PRIMARY KEY)'))
        await connection.execute(
            text(f'CREATE TABLE "{schema}".products (id uuid PRIMARY KEY, sku text, situacao text)')
        )
        for model in (
            Integration,
            ProductLink,
            Listing,
            SyncLog,
            CarrefourOfferWrite,
            CarrefourOfferBatch,
        ):
            await connection.run_sync(
                lambda sync, model=model: model.__table__.create(sync, checkfirst=True)
            )
    monkeypatch.setattr(db, "SessionLocal", sessions)
    clock = [datetime.now(UTC) + timedelta(seconds=10)]
    monkeypatch.setattr(queue, "_now", lambda: clock[0])
    remote = SimpleNamespace(
        source_stock=None,
        source_failure=False,
        offers={},
        posts=[],
        gets=[],
        imports=[],
        post_error=None,
        posting=None,
        preparing=None,
        report={
            "status": "COMPLETE",
            "lines_in_error": 0,
            "lines_in_pending": 0,
            "lines_in_success": 1,
            "has_error_report": False,
        },
    )

    class FakeClient:
        def __init__(self, creds, integration_id=None):
            self.creds = dict(creds)

        async def test_connection(self):
            return SimpleNamespace(ok=True)

        async def get_offer(self, offer_id):
            remote.gets.append(str(offer_id))
            if remote.preparing is not None:
                callback, remote.preparing = remote.preparing, None
                await callback()
            return copy.deepcopy(remote.offers[str(offer_id)])

        async def submit_stock_updates(self, offers):
            remote.posts.append(copy.deepcopy(offers))
            if remote.posting is not None:
                await remote.posting()
            if remote.post_error:
                raise remote.post_error
            return "777"

        async def submit_price_updates(self, offers):
            return await self.submit_stock_updates(offers)

        async def get_stock_import(self, import_id):
            remote.imports.append(import_id)
            return dict(remote.report)

        async def get_price_import(self, import_id):
            return await self.get_stock_import(import_id)

    monkeypatch.setattr(queue, "CarrefourClient", FakeClient)

    def stock_builder(offer, *, qty, expected_sku):
        assert offer["shop_sku"] == expected_sku
        return {"shop_sku": expected_sku, "quantity": qty}

    def price_builder(offer, *, price, expected_sku):
        assert offer["shop_sku"] == expected_sku
        return {"shop_sku": expected_sku, "price": str(price)}

    monkeypatch.setattr(carrefour, "build_stock_update", stock_builder, raising=False)
    monkeypatch.setattr(carrefour, "build_price_update", price_builder, raising=False)

    async def insert(
        *,
        stock_flag=True,
        price_flag=True,
        vacation=False,
        with_bling=True,
        sku="sku-1",
        offer_id="123",
    ):
        async with sessions.begin() as session:
            owner, product = uuid4(), uuid4()
            await session.execute(
                text(f'INSERT INTO "{schema}".users (id) VALUES (:id)'), {"id": owner}
            )
            await session.execute(
                text(f'INSERT INTO "{schema}".products (id, sku) VALUES (:id, :sku)'),
                {"id": product, "sku": sku},
            )
            integration = Integration(
                user_id=owner,
                platform=IntegrationPlatform.CARREFOUR,
                name="TEST ONLY",
                status="active",
                vacation_mode=vacation,
                credentials=encrypt_json(
                    {
                        "api_key": "fake",
                        "shop_id": "1234",
                        queue.STOCK_SCOPE: stock_flag,
                        queue.PRICE_SCOPE: price_flag,
                    }
                ),
            )
            session.add(integration)
            await session.flush()
            link = ProductLink(
                user_id=owner,
                product_id=product,
                integration_id=integration.id,
                platform=IntegrationPlatform.CARREFOUR,
                external_id=offer_id,
                external_sku=sku,
                stock=7,
                price=Decimal("25.00"),
            )
            listing = Listing(
                user_id=owner,
                integration_id=integration.id,
                platform=IntegrationPlatform.CARREFOUR,
                external_id=offer_id,
                sku=sku,
                title="Test",
                stock=7,
                price=2500,
            )
            session.add_all([link, listing])
            if with_bling:
                bling = Integration(
                    user_id=owner,
                    platform=IntegrationPlatform.BLING,
                    name="TEST BLING",
                    status="active",
                    credentials=encrypt_json({"fake": True}),
                )
                session.add(bling)
                await session.flush()
                session.add(
                    ProductLink(
                        user_id=owner,
                        product_id=product,
                        integration_id=bling.id,
                        platform=IntegrationPlatform.BLING,
                        external_id="789",
                        external_sku=sku,
                    )
                )
            await session.flush()
            remote.offers[offer_id] = {
                "offer_id": int(offer_id),
                "shop_sku": sku,
                "quantity": 7,
                "price": "25.00",
            }
            return integration.id, link

    async def read():
        async with sessions() as session:
            return SimpleNamespace(
                rows=(await session.scalars(select(CarrefourOfferWrite))).all(),
                batches=(
                    await session.scalars(
                        select(CarrefourOfferBatch).order_by(CarrefourOfferBatch.created_at)
                    )
                ).all(),
                links=(
                    await session.scalars(
                        select(ProductLink).where(
                            ProductLink.platform == IntegrationPlatform.CARREFOUR
                        )
                    )
                ).all(),
                listings=(await session.scalars(select(Listing))).all(),
                logs=(await session.scalars(select(SyncLog))).all(),
            )

    async def edit(integration_id, **values):
        async with sessions.begin() as session:
            record = await session.get(Integration, integration_id)
            creds = decrypt_json(record.credentials)
            for key, value in values.items():
                if key in {queue.STOCK_SCOPE, queue.PRICE_SCOPE, "shop_id"}:
                    creds[key] = value
                else:
                    setattr(record, key, value)
            record.credentials = encrypt_json(creds)

    async def refresh_source(link_id):
        if remote.source_failure:
            return None
        if remote.source_stock is not None:
            return remote.source_stock
        async with sessions() as session:
            return await session.scalar(
                select(CarrefourOfferWrite.requested_stock).where(
                    CarrefourOfferWrite.link_id == link_id
                )
            )

    monkeypatch.setattr(queue, "_refresh_stock_source", refresh_source)

    try:
        yield SimpleNamespace(
            insert=insert,
            read=read,
            edit=edit,
            remote=remote,
            sessions=sessions,
            engine=engine,
            schema=schema,
            clock=clock,
        )
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await engine.dispose()


async def test_opt_in_and_link_identity_before_any_http(env):
    ident, link = await env.insert(stock_flag=False, price_flag=False)
    for flags in ({"qty": 0}, {"price": Decimal("30")}):
        result = await queue.enqueue_offer_write(integration_id=ident, link=link, **flags)
        assert result.error_code == "carrefour_write_not_enabled"
    result = await queue.enqueue_offer_write(integration_id=uuid4(), link=link, qty=0)
    assert result.error_code == "carrefour_link_mismatch"
    await queue.run_carrefour_offer_writes()
    assert not (await env.read()).rows and not env.remote.posts and not env.remote.gets


async def test_accepted_import_never_marks_ok_until_readback(env):
    ident, link = await env.insert()
    result = await queue.enqueue_offer_write(integration_id=ident, link=link, qty=0)
    assert (
        result.status == SyncStatus.REQUIRES_REVIEW
        and result.error_code == "carrefour_write_pending"
    )
    assert (await queue.run_carrefour_offer_writes())["errors"] == 0
    state = await env.read()
    assert state.batches[0].state == "submitted"
    assert state.links[0].stock == 7 and state.listings[0].price == 2500 and not state.logs
    await queue.run_carrefour_offer_writes()
    assert not env.remote.imports
    env.clock[0] += timedelta(minutes=5)
    env.remote.offers["123"].update(quantity=0, price="30.00")
    assert (await queue.run_carrefour_offer_writes())["errors"] == 0
    state = await env.read()
    assert state.rows[0].status == "complete" and state.batches[0].state == "complete"
    assert state.links[0].stock == 0 and state.links[0].price == Decimal("25")
    assert state.listings[0].stock == 0 and state.listings[0].price == 2500
    assert len(state.logs) == 1 and all(log.status == LinkSyncStatus.OK for log in state.logs)


@pytest.mark.parametrize(
    "report",
    [
        {"status": "COMPLETE", "lines_in_error": 1, "lines_in_pending": 0},
        {"status": "COMPLETE", "lines_in_error": 0, "lines_in_pending": 1},
        {
            "status": "COMPLETE",
            "lines_in_error": 0,
            "lines_in_pending": 0,
            "has_error_report": True,
        },
    ],
)
async def test_complete_with_errors_or_pending_is_not_success(env, report):
    ident, link = await env.insert()
    await queue.enqueue_offer_write(integration_id=ident, link=link, qty=10)
    await queue.run_carrefour_offer_writes()
    env.clock[0] += timedelta(minutes=5)
    env.remote.report = report
    await queue.run_carrefour_offer_writes()
    state = await env.read()
    assert state.rows[0].status == "review" and state.links[0].stock == 7
    assert not any(log.status == LinkSyncStatus.OK for log in state.logs)


async def test_concurrent_workers_submit_once(env):
    ident, link = await env.insert()
    await queue.enqueue_offer_write(integration_id=ident, link=link, qty=10)
    results = await asyncio.gather(*[queue.run_carrefour_offer_writes() for _ in range(5)])
    assert not any(x["errors"] for x in results)
    assert len(env.remote.posts) == 1
    assert len((await env.read()).batches) == 1


async def test_new_price_during_stock_import_does_not_resend_confirmed_stock(env):
    ident, link = await env.insert()
    await queue.enqueue_offer_write(integration_id=ident, link=link, qty=10)
    await queue.run_carrefour_offer_writes()
    await queue.enqueue_offer_write(integration_id=ident, link=link, price=Decimal("30"))
    env.clock[0] += timedelta(minutes=5)
    env.remote.offers["123"]["quantity"] = 10
    await queue.run_carrefour_offer_writes()
    state = await env.read()
    assert state.rows[0].status == "submitted" and state.rows[0].requested_stock is None
    assert state.rows[0].requested_price == Decimal("30")
    env.remote.offers["123"]["quantity"] = 9  # sale since the first write
    await queue.run_carrefour_offer_writes()
    assert len(env.remote.posts) == 2
    assert "quantity" not in env.remote.posts[1][0]


async def test_superseded_before_submission_never_sends_obsolete_stock(env):
    ident, link = await env.insert()
    await queue.enqueue_offer_write(integration_id=ident, link=link, qty=10)

    async def change():
        await queue.enqueue_offer_write(integration_id=ident, link=link, qty=0)

    env.remote.preparing = change
    await queue.run_carrefour_offer_writes()
    assert not env.remote.posts
    state = await env.read()
    assert state.rows[0].requested_stock == 0 and state.rows[0].status == "queued"
    env.clock[0] += timedelta(minutes=5)
    await queue.run_carrefour_offer_writes()
    assert env.remote.posts[0][0]["quantity"] == 0


async def test_unknown_post_is_durable_and_never_retried(env):
    ident, link = await env.insert()
    await queue.enqueue_offer_write(integration_id=ident, link=link, qty=10)
    env.remote.post_error = carrefour.CarrefourError("submission_unknown", "safe")
    await queue.run_carrefour_offer_writes()
    state = await env.read()
    assert state.batches[0].state == "uncertain" and state.rows[0].status == "review"
    result = await queue.enqueue_offer_write(integration_id=ident, link=link, qty=10)
    assert result.error_code == "carrefour_submission_unknown"
    env.clock[0] += timedelta(hours=1)
    await queue.run_carrefour_offer_writes()
    assert len(env.remote.posts) == 1


async def test_greater_readback_requires_review_without_automatic_restock(env):
    ident, link = await env.insert()
    await queue.enqueue_offer_write(integration_id=ident, link=link, qty=10)
    await queue.run_carrefour_offer_writes()
    env.clock[0] += timedelta(minutes=5)
    env.remote.offers["123"]["quantity"] = 11
    await queue.run_carrefour_offer_writes()
    result = await queue.enqueue_offer_write(integration_id=ident, link=link, qty=10)
    assert result.error_code == "carrefour_write_requires_review"
    await queue.run_carrefour_offer_writes()
    assert len(env.remote.posts) == 1 and (await env.read()).links[0].stock == 7


async def test_vacation_pauses_stock_but_sends_price_preserving_pending_stock(env):
    ident, link = await env.insert()
    await queue.enqueue_offer_write(integration_id=ident, link=link, qty=10, price=Decimal("30"))
    await env.edit(ident, vacation_mode=True)
    await queue.run_carrefour_offer_writes()
    assert "quantity" not in env.remote.posts[0][0]
    assert env.remote.posts[0][0]["price"] == "30.00"
    env.clock[0] += timedelta(minutes=5)
    env.remote.offers["123"]["price"] = "30"
    await queue.run_carrefour_offer_writes()
    state = await env.read()
    assert state.rows[0].requested_stock == 10 and state.rows[0].requested_price is None
    assert state.rows[0].status == "queued" and state.links[0].stock == 7


async def test_disabling_scope_while_preparing_prevents_submission(env):
    ident, link = await env.insert()
    await queue.enqueue_offer_write(integration_id=ident, link=link, qty=10)

    async def disable():
        await env.edit(ident, **{queue.STOCK_SCOPE: False})

    env.remote.preparing = disable
    await queue.run_carrefour_offer_writes()
    assert not env.remote.posts and (await env.read()).rows[0].status == "review"


async def test_shop_change_before_confirmation_never_reads_other_shop(env):
    ident, link = await env.insert()
    await queue.enqueue_offer_write(integration_id=ident, link=link, qty=10)
    await queue.run_carrefour_offer_writes()
    env.clock[0] += timedelta(minutes=5)
    await env.edit(ident, shop_id="999")
    await queue.run_carrefour_offer_writes()
    assert not env.remote.imports
    assert (await env.read()).batches[0].state == "uncertain"


async def test_matching_remote_target_finishes_without_post(env):
    ident, link = await env.insert()
    await queue.enqueue_offer_write(integration_id=ident, link=link, qty=7)
    await queue.run_carrefour_offer_writes()
    state = await env.read()
    assert not env.remote.posts and state.rows[0].status == "complete"
    assert len(state.logs) == 1


async def test_only_bling_linked_products_are_allowed(env):
    ident, link = await env.insert(with_bling=False)
    for kwargs in ({"qty": 10}, {"price": Decimal("30")}):
        result = await queue.enqueue_offer_write(integration_id=ident, link=link, **kwargs)
        assert result.error_code == "carrefour_bling_source_required"
    assert not (await env.read()).rows


async def test_queued_stock_is_replaced_by_fresh_bling_balance(env):
    ident, link = await env.insert()
    await queue.enqueue_offer_write(integration_id=ident, link=link, qty=10)
    env.remote.source_stock = 0
    await queue.run_carrefour_offer_writes()
    state = await env.read()
    assert env.remote.posts[0][0]["quantity"] == 0
    assert state.rows[0].requested_stock == 0 and state.rows[0].revision == 2
    assert "price" not in env.remote.posts[0][0]


async def test_failed_bling_refresh_never_posts_or_assumes_zero(env):
    ident, link = await env.insert()
    await queue.enqueue_offer_write(integration_id=ident, link=link, qty=10)
    env.remote.source_failure = True
    await queue.run_carrefour_offer_writes()
    state = await env.read()
    assert not env.remote.posts and not env.remote.gets
    assert state.rows[0].status == "review" and state.rows[0].requested_stock == 10
    assert state.rows[0].last_error == "carrefour_bling_refresh_failed"
    assert state.links[0].last_sync_status == LinkSyncStatus.REQUIRES_REVIEW


async def test_stock_freshness_is_rechecked_before_post(env, monkeypatch):
    ident, link = await env.insert()
    await queue.enqueue_offer_write(integration_id=ident, link=link, qty=10)
    real_submit = queue._submit

    async def delayed_submit(batch_id, creds):
        env.clock[0] += timedelta(minutes=6)
        await real_submit(batch_id, creds)

    monkeypatch.setattr(queue, "_submit", delayed_submit)
    await queue.run_carrefour_offer_writes()
    assert not env.remote.posts
    assert (await env.read()).rows[0].status == "queued"


async def test_lower_stock_after_confirmed_import_saves_observed_balance(env):
    ident, link = await env.insert()
    await queue.enqueue_offer_write(integration_id=ident, link=link, qty=10)
    await queue.run_carrefour_offer_writes()
    env.clock[0] += timedelta(minutes=5)
    env.remote.offers["123"]["quantity"] = 9
    await queue.run_carrefour_offer_writes()
    state = await env.read()
    assert state.rows[0].status == "complete" and state.rows[0].requested_stock is None
    assert state.links[0].stock == state.listings[0].stock == state.logs[0].qty_after == 9
    env.clock[0] += timedelta(minutes=5)
    await queue.run_carrefour_offer_writes()
    assert len(env.remote.posts) == 1


async def test_zero_processed_rows_cannot_confirm_lower_stock(env):
    ident, link = await env.insert()
    await queue.enqueue_offer_write(integration_id=ident, link=link, qty=10)
    await queue.run_carrefour_offer_writes()
    env.clock[0] += timedelta(minutes=5)
    env.remote.report["lines_in_success"] = 0
    env.remote.offers["123"]["quantity"] = 9
    await queue.run_carrefour_offer_writes()
    state = await env.read()
    assert state.rows[0].status == "review" and state.links[0].stock == 7


async def test_new_stock_intent_survives_old_confirmation(env):
    ident, link = await env.insert()
    await queue.enqueue_offer_write(integration_id=ident, link=link, qty=10)
    await queue.run_carrefour_offer_writes()
    await queue.enqueue_offer_write(integration_id=ident, link=link, qty=0)
    env.clock[0] += timedelta(minutes=5)
    env.remote.offers["123"]["quantity"] = 10
    await queue.run_carrefour_offer_writes()
    state = await env.read()
    assert state.rows[0].requested_stock == 0 and state.rows[0].status == "submitted"
    await queue.run_carrefour_offer_writes()
    assert env.remote.posts[1][0]["quantity"] == 0


async def test_two_concurrent_submit_calls_refresh_state_after_lock(env, monkeypatch):
    ident, link = await env.insert()
    await queue.enqueue_offer_write(integration_id=ident, link=link, qty=10)
    real_submit = queue._submit

    async def defer_submit(*args):
        pass

    monkeypatch.setattr(queue, "_submit", defer_submit)
    await queue.run_carrefour_offer_writes()
    batch = (await env.read()).batches[0]
    assert batch.state == "submitting"
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold():
        entered.set()
        await asyncio.wait_for(release.wait(), timeout=5)

    env.remote.posting = hold
    creds = {"api_key": "fake", "shop_id": "1234"}
    first = asyncio.create_task(real_submit(batch.id, creds))
    await asyncio.wait_for(entered.wait(), timeout=5)
    second = asyncio.create_task(real_submit(batch.id, creds))
    await asyncio.sleep(0.05)
    release.set()
    await asyncio.wait_for(asyncio.gather(first, second), timeout=5)
    assert len(env.remote.posts) == 1


async def test_process_crash_after_write_ahead_fence_never_replays(env):
    ident, link = await env.insert()
    await queue.enqueue_offer_write(integration_id=ident, link=link, qty=10)
    env.remote.post_error = RuntimeError("simulated process interruption")
    assert (await queue.run_carrefour_offer_writes())["errors"] == 1
    assert (await env.read()).batches[0].state == "submitting"
    env.clock[0] += timedelta(minutes=20)
    await queue.run_carrefour_offer_writes()
    assert (await env.read()).batches[0].state == "uncertain"
    assert len(env.remote.posts) == 1


async def test_migration_up_down_and_unresolved_guard(env, monkeypatch):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy.exc import DBAPIError

    path = Path(__file__).resolve().parents[2] / "alembic/versions/0498_carrefour_offer_write.py"
    spec = importlib.util.spec_from_file_location("isolated_carrefour_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    monkeypatch.setattr(migration, "SCHEMA", env.schema)

    async with env.engine.begin() as connection:
        for model in (CarrefourOfferBatch, CarrefourOfferWrite):
            await connection.run_sync(lambda sync, model=model: model.__table__.drop(sync))

        def upgrade(sync):
            with Operations.context(MigrationContext.configure(sync)):
                migration.upgrade()

        await connection.run_sync(upgrade)

    ident, link = await env.insert()
    await queue.enqueue_offer_write(integration_id=ident, link=link, qty=10)
    await queue.run_carrefour_offer_writes()

    def downgrade(sync):
        with Operations.context(MigrationContext.configure(sync)):
            migration.downgrade()

    with pytest.raises(DBAPIError, match="Reconcile Carrefour imports"):
        async with env.engine.begin() as connection:
            await connection.run_sync(downgrade)
    async with env.sessions.begin() as session:
        batch = await session.scalar(select(CarrefourOfferBatch))
        batch.state = "complete"
    async with env.engine.begin() as connection:
        await connection.run_sync(downgrade)
        tables = (
            await connection.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname=:schema "
                    "AND tablename LIKE 'carrefour_offer_%'"
                ),
                {"schema": env.schema},
            )
        ).all()
        assert not tables


async def test_fifty_offer_batches_confirm_then_dispatch_once_in_same_tick(env):
    ident, first = await env.insert()
    links = [first]
    async with env.sessions.begin() as session:
        bling_id = await session.scalar(
            select(Integration.id).where(
                Integration.user_id == first.user_id,
                Integration.platform == IntegrationPlatform.BLING,
            )
        )
        for i in range(50):
            sku, offer_id, product_id = f"extra-{i}", str(124 + i), uuid4()
            await session.execute(
                text(f'INSERT INTO "{env.schema}".products (id, sku) VALUES (:id, :sku)'),
                {"id": product_id, "sku": sku},
            )
            link = ProductLink(
                user_id=first.user_id,
                product_id=product_id,
                integration_id=ident,
                platform=IntegrationPlatform.CARREFOUR,
                external_id=offer_id,
                external_sku=sku,
                stock=7,
                price=Decimal("25"),
            )
            session.add_all(
                [
                    link,
                    ProductLink(
                        user_id=first.user_id,
                        product_id=product_id,
                        integration_id=bling_id,
                        platform=IntegrationPlatform.BLING,
                        external_id=str(800 + i),
                        external_sku=sku,
                    ),
                ]
            )
            links.append(link)
            env.remote.offers[offer_id] = {
                "offer_id": int(offer_id),
                "shop_sku": sku,
                "quantity": 7,
                "price": "25.00",
            }
        await session.flush()
    for link in links:
        result = await queue.enqueue_offer_write(integration_id=ident, link=link, qty=10)
        assert result.error_code == "carrefour_write_pending"

    assert (await queue.run_carrefour_offer_writes())["errors"] == 0
    assert len(env.remote.posts) == 1 and len(env.remote.posts[0]) == 50
    state = await env.read()
    assert sum(row.status == "submitted" for row in state.rows) == 50
    assert sum(row.status == "queued" for row in state.rows) == 1

    env.clock[0] += timedelta(minutes=4)
    await queue.run_carrefour_offer_writes()
    assert len(env.remote.posts) == 1 and not env.remote.imports
    sent_skus = {item["shop_sku"] for item in env.remote.posts[0]}
    for offer in env.remote.offers.values():
        if offer["shop_sku"] in sent_skus:
            offer["quantity"] = 10
    env.remote.report["lines_in_success"] = 50
    env.clock[0] += timedelta(minutes=1)
    assert (await queue.run_carrefour_offer_writes())["errors"] == 0
    assert len(env.remote.posts) == 2 and len(env.remote.posts[1]) == 1
    assert env.remote.posts[1][0]["shop_sku"] not in sent_skus
    state = await env.read()
    assert sum(row.status == "complete" for row in state.rows) == 50
    assert sum(row.status == "submitted" for row in state.rows) == 1
    await queue.run_carrefour_offer_writes()
    assert len(env.remote.posts) == 2


async def test_failed_confirmation_does_not_dispatch_followup_or_retry(env):
    ident, link = await env.insert()
    await queue.enqueue_offer_write(integration_id=ident, link=link, qty=10, price=Decimal("30"))
    await queue.run_carrefour_offer_writes()
    env.clock[0] += timedelta(minutes=5)
    env.remote.report["lines_in_error"] = 1
    await queue.run_carrefour_offer_writes()
    assert len(env.remote.posts) == 1
    state = await env.read()
    assert state.rows[0].status == "review" and state.rows[0].requested_price == Decimal("30")
    env.clock[0] += timedelta(minutes=5)
    await queue.run_carrefour_offer_writes()
    assert len(env.remote.posts) == 1


async def test_enqueue_while_caller_has_flushed_integration_foreign_key(env):
    """SyncOrchestrator batches SyncLogs in an outer session, as do pricing callers."""
    ident, link = await env.insert()
    async with env.sessions.begin() as outer:
        outer.add(
            SyncLog(
                user_id=link.user_id,
                product_id=link.product_id,
                product_link_id=link.id,
                integration_id=ident,
                platform=IntegrationPlatform.CARREFOUR,
                action=SyncLogAction.UPDATE_STOCK,
                status=LinkSyncStatus.PENDING,
                payload={"caller": "outer-transaction"},
            )
        )
        await outer.flush()  # FK verification holds KEY SHARE on the integration.
        result = await asyncio.wait_for(
            queue.enqueue_offer_write(integration_id=ident, link=link, qty=10),
            timeout=2,
        )
        assert result.error_code == "carrefour_write_pending"
    assert (await env.read()).rows[0].requested_stock == 10


async def test_no_key_update_still_blocks_scope_changes_during_post(env):
    ident, link = await env.insert()
    await queue.enqueue_offer_write(integration_id=ident, link=link, qty=10)
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold_post():
        entered.set()
        await asyncio.wait_for(release.wait(), timeout=5)

    env.remote.posting = hold_post
    sending = asyncio.create_task(queue.run_carrefour_offer_writes())
    await asyncio.wait_for(entered.wait(), timeout=5)
    disable = asyncio.create_task(env.edit(ident, **{queue.STOCK_SCOPE: False}))
    try:
        await asyncio.sleep(0.05)
        assert not disable.done(), "scope updates must wait until the active POST is committed"
    finally:
        release.set()
    sent, _ = await asyncio.wait_for(asyncio.gather(sending, disable), timeout=5)
    assert sent["errors"] == 0 and len(env.remote.posts) == 1
    async with env.sessions() as session:
        integration = await session.get(Integration, ident)
        assert decrypt_json(integration.credentials)[queue.STOCK_SCOPE] is False
