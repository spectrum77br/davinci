"""Real database/dispatch-trigger checks; remote APIs are strictly mocked."""

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingEnvioCorrecao,
    BlingEnvioEvento,
    BlingOrder,
    IntegrationPlatform,
    MargemAudit,
)
from app.services import marketplace_shipment_check as sweep
from app.services.magalu_shipment_status import MagaluShipmentStatus
from app.services.magalu_shipment_sync import apply_magalu_shipment

BID = 99880011
CODE = "1234567891234567"
POSTED = datetime(2026, 9, 30, 19, 27, 29, tzinfo=UTC)


class Bling:
    def __init__(self, *, before=21, after=15, patch_error=None, overrides=None):
        self.before, self.after = before, after
        self.reads = 0
        self.writes = []
        self.patch_error = patch_error
        self.overrides = overrides or {}

    async def _request(self, method, path):
        assert method == "GET" and path == f"/pedidos/vendas/{BID}"
        state = self.before if self.reads == 0 else self.after
        self.reads += 1
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": BID,
                    "numero": 123456,
                    "numeroLoja": "LU-" + CODE,
                    "loja": {"id": 456},
                    "situacao": {"id": state},
                    **self.overrides,
                }
            },
        )

    async def update_order_situacao(self, bid, state):
        self.writes.append((bid, state))
        if self.patch_error:
            request = httpx.Request("PATCH", f"https://bling.test/{bid}/situacoes/{state}")
            response = httpx.Response(self.patch_error, request=request)
            raise httpx.HTTPStatusError("rejected", request=request, response=response)
        return True


async def order(db, *, count=1, state="21"):
    rows = [
        BlingOrder(
            bling_id=BID,
            numero="123456",
            numeroloja="LU-" + CODE,
            loja="456",
            item_index=index,
            item_codigo=f"bag-{index}",
            item_quantidade=1,
            situacao=state,
            em_andamento_data=date(2026, 9, 28),
        )
        for index in range(count)
    ]
    db.add_all(rows)
    await db.commit()
    return rows


async def events(db):
    return (
        await db.scalars(select(BlingEnvioEvento).where(BlingEnvioEvento.bling_id == BID))
    ).all()


@pytest.mark.asyncio
async def test_confirmed_shipment_moves_all_items_preserves_real_time_and_is_idempotent(db):
    rows = await order(db, count=2)
    bling = Bling()
    confirmation = MagaluShipmentStatus(confirmed=True, shipped_at=POSTED)
    applied = await apply_magalu_shipment(db, bling, rows[0], confirmation)
    await db.commit()
    assert applied.local_updated == 2 and applied.bling_updated and not applied.error
    assert bling.writes == [(BID, 15)] and bling.reads == 2
    for row in rows:
        await db.refresh(row)
        assert row.situacao == "15" and row.em_andamento_data == date(2026, 9, 30)
    ledger = await events(db)
    assert len(ledger) == 2
    assert all(
        event.occurred_at == POSTED and event.shipping_day == date(2026, 9, 30) for event in ledger
    )
    assert not (await db.scalars(select(BlingEnvioCorrecao))).all()
    assert len((await db.scalars(select(MargemAudit))).all()) == 2
    second = await apply_magalu_shipment(db, bling, rows[0], confirmation)
    assert second.local_updated == 0 and bling.writes == [(BID, 15)]
    assert all(event.occurred_at == POSTED for event in await events(db))


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [400, 409, 422, 500])
async def test_bling_error_does_not_turn_order_green_without_readback(db, code):
    rows = await order(db)
    applied = await apply_magalu_shipment(
        db,
        Bling(after=21, patch_error=code),
        rows[0],
        MagaluShipmentStatus(confirmed=True, shipped_at=POSTED),
    )
    assert applied.error and not applied.local_updated
    assert rows[0].situacao == "21" and not await events(db)
    assert not (await db.scalars(select(MargemAudit))).all()


@pytest.mark.asyncio
async def test_patch_accepted_but_readback_unchanged_stays_red(db):
    rows = await order(db)
    result = await apply_magalu_shipment(
        db, Bling(after=21), rows[0], MagaluShipmentStatus(confirmed=True, shipped_at=POSTED)
    )
    assert result.error and not result.local_updated and not await events(db)


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_state", [12, 9, 83957])
async def test_does_not_downgrade_cancellation_or_later_bling_state(db, remote_state):
    rows = await order(db)
    bling = Bling(before=remote_state)
    result = await apply_magalu_shipment(
        db, bling, rows[0], MagaluShipmentStatus(confirmed=True, shipped_at=POSTED)
    )
    assert result.reason == "bling_state_changed" and not bling.writes
    assert rows[0].situacao == "21" and not await events(db)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides", [{"numeroLoja": "LU-wrong"}, {"loja": {"id": 789}}, {"id": BID + 1}]
)
async def test_wrong_order_or_store_never_patches_bling(db, overrides):
    rows = await order(db)
    bling = Bling(overrides=overrides)
    result = await apply_magalu_shipment(
        db, bling, rows[0], MagaluShipmentStatus(confirmed=True, shipped_at=POSTED)
    )
    assert result.error and not bling.writes and not await events(db)


@pytest.mark.asyncio
async def test_preexisting_dispatch_followed_by_manual_rollback_is_untouched(db):
    rows = await order(db, state="15")
    before = (await events(db))[0].occurred_at
    rows[0].situacao = "21"
    await db.commit()
    bling = Bling()
    result = await apply_magalu_shipment(
        db, bling, rows[0], MagaluShipmentStatus(confirmed=True, shipped_at=POSTED)
    )
    assert result.reason == "historical_dispatch_exists" and bling.reads == 0
    assert (await events(db))[0].occurred_at == before
    assert not (await db.scalars(select(BlingEnvioCorrecao))).all()


@pytest.mark.asyncio
async def test_confirmed_without_date_does_not_invent_original_posting_time(db):
    rows = await order(db)
    before = datetime.now(UTC)
    result = await apply_magalu_shipment(db, Bling(), rows[0], MagaluShipmentStatus(confirmed=True))
    await db.commit()
    assert result.local_updated == 1 and rows[0].em_andamento_data == date(2026, 9, 28)
    assert (await events(db))[0].occurred_at >= before - timedelta(seconds=1)


@pytest.mark.asyncio
async def test_unconfirmed_status_has_no_reads_or_writes(db):
    rows = await order(db)
    bling = Bling()
    result = await apply_magalu_shipment(db, bling, rows[0], MagaluShipmentStatus(confirmed=False))
    assert not result.local_updated and bling.reads == 0 and not bling.writes


@pytest.mark.asyncio
async def test_existing_scheduled_sweep_routes_magalu_through_verified_apply(db, monkeypatch):
    rows = await order(db)
    integration = SimpleNamespace(
        id=uuid4(),
        platform=IntegrationPlatform.MAGALU,
        credentials=b"test",
        archived_at=None,
        status="active",
    )
    bling = Bling()

    @asynccontextmanager
    async def scope():
        yield db
        await db.commit()

    monkeypatch.setattr(sweep, "session_scope", scope)
    monkeypatch.setattr(sweep, "_load_candidates", AsyncMock(return_value=rows))
    monkeypatch.setattr(sweep, "_get_bling_integration", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        sweep, "_resolve_store_and_integration", AsyncMock(return_value=(object(), integration))
    )
    monkeypatch.setattr(sweep, "_build_bling_client", AsyncMock(return_value=bling))
    monkeypatch.setattr(sweep, "decrypt_json", lambda _: {})
    monkeypatch.setattr(sweep, "MagaluClient", lambda *args, **kwargs: object())
    check = AsyncMock(return_value=MagaluShipmentStatus(confirmed=True, shipped_at=POSTED))
    monkeypatch.setattr(sweep, "get_magalu_shipment_status", check)
    alert = AsyncMock(side_effect=AssertionError("Magalu must not send notifications"))
    monkeypatch.setattr(sweep, "_avisar_threema", alert)
    result = await sweep.run_check_marketplace_shipped_orders()
    assert result["local_updated"] == 1 and result["bling_updated"] == 1 and result["errors"] == 0
    assert (await events(db))[0].occurred_at == POSTED
    alert.assert_not_called()
    assert check.await_args.args[1] == "LU-" + CODE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason, expected_errors", [("http_error", 1), ("request_failed", 1), ("not_shipped", 0)]
)
async def test_sweep_counts_magalu_read_failures_without_changing_orders(
    db, monkeypatch, reason, expected_errors
):
    rows = await order(db)
    integration = SimpleNamespace(
        id=uuid4(),
        platform=IntegrationPlatform.MAGALU,
        credentials=b"test",
        archived_at=None,
        status="active",
    )

    @asynccontextmanager
    async def scope():
        yield db
        await db.commit()

    monkeypatch.setattr(sweep, "session_scope", scope)
    monkeypatch.setattr(sweep, "_load_candidates", AsyncMock(return_value=rows))
    monkeypatch.setattr(sweep, "_get_bling_integration", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        sweep, "_resolve_store_and_integration", AsyncMock(return_value=(object(), integration))
    )
    build = AsyncMock(side_effect=AssertionError("No Bling client when Magalu unconfirmed"))
    monkeypatch.setattr(sweep, "_build_bling_client", build)
    monkeypatch.setattr(sweep, "decrypt_json", lambda _: {})
    monkeypatch.setattr(sweep, "MagaluClient", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        sweep,
        "get_magalu_shipment_status",
        AsyncMock(return_value=MagaluShipmentStatus(reason=reason)),
    )
    result = await sweep.run_check_marketplace_shipped_orders()
    assert result["errors"] == expected_errors and result["local_updated"] == 0
    build.assert_not_called()
    assert rows[0].situacao == "21" and not await events(db)


@pytest.mark.asyncio
async def test_magalu_bling_client_uses_durable_refresh_without_session_callback(monkeypatch):
    monkeypatch.setattr(sweep, "decrypt_json", lambda _: {})
    session = SimpleNamespace(flush=AsyncMock())
    integration = SimpleNamespace(credentials=b"test", id=uuid4())
    client = await sweep._build_bling_client(session, integration, persist_in_session=False)
    assert client._on_refresh is None and client._integration_id == integration.id
    session.flush.assert_not_called()


@pytest.mark.asyncio
async def test_concurrent_ingest_lock_defers_without_remote_writes(db):
    rows = await order(db)
    bling = Bling()
    async with AsyncSession(bind=db.bind) as concurrent:
        await concurrent.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"ingest_bling_order:{BID}"},
        )
        result = await apply_magalu_shipment(
            db, bling, rows[0], MagaluShipmentStatus(confirmed=True, shipped_at=POSTED)
        )
        assert result.reason == "ingest_running" and bling.reads == 0


@pytest.mark.asyncio
async def test_rereads_local_cancellation_after_candidate_was_loaded(db):
    rows = await order(db)
    async with AsyncSession(bind=db.bind) as concurrent:
        await concurrent.execute(
            update(BlingOrder).where(BlingOrder.bling_id == BID).values(situacao="12")
        )
        await concurrent.commit()
    assert rows[0].situacao == "21"  # stale candidate held by the sweep
    bling = Bling()
    result = await apply_magalu_shipment(
        db, bling, rows[0], MagaluShipmentStatus(confirmed=True, shipped_at=POSTED)
    )
    assert result.reason == "local_state_changed" and bling.reads == 0
    assert rows[0].situacao == "12" and not await events(db)


@pytest.mark.asyncio
async def test_sweep_reuses_rotated_bling_client_across_magalu_and_other_store(db, monkeypatch):
    rows = await order(db)
    second = BlingOrder(
        bling_id=BID + 1,
        numero="123457",
        numeroloja="OTHER-ORDER",
        loja="789",
        item_index=0,
        item_codigo="other",
        situacao="21",
        em_andamento_data=date(2026, 9, 28),
    )
    db.add(second)
    await db.commit()
    magalu = SimpleNamespace(platform=IntegrationPlatform.MAGALU)
    shopee = SimpleNamespace(platform=IntegrationPlatform.SHOPEE)

    class RotatingBling(Bling):
        rotated = False

        async def update_order_situacao(self, bid, state):
            if bid == BID:
                self.rotated = True
            else:
                assert self.rotated, "another store received a stale client/token"
            self.writes.append((bid, state))
            return True

    built = []

    async def build(*args, **kwargs):
        assert kwargs["persist_in_session"] is False
        client = RotatingBling()
        built.append(client)
        return client

    async def check(session, integration, orders, deadlines, **kwargs):
        if integration.platform == IntegrationPlatform.MAGALU:
            kwargs["magalu_confirmations"][BID] = MagaluShipmentStatus(
                confirmed=True, shipped_at=POSTED
            )
            return {BID: POSTED.date()}
        return {BID + 1: POSTED.date()}

    @asynccontextmanager
    async def scope():
        yield db
        await db.commit()

    monkeypatch.setattr(sweep, "session_scope", scope)
    monkeypatch.setattr(sweep, "_load_candidates", AsyncMock(return_value=[rows[0], second]))
    monkeypatch.setattr(sweep, "_get_bling_integration", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        sweep,
        "_resolve_store_and_integration",
        AsyncMock(side_effect=[(object(), magalu), (object(), shopee)]),
    )
    monkeypatch.setattr(sweep, "_build_bling_client", build)
    monkeypatch.setattr(sweep, "_check_marketplace_shipped", check)
    result = await sweep.run_check_marketplace_shipped_orders()
    assert result["local_updated"] == 2 and result["errors"] == 0
    assert len(built) == 1 and built[0].writes == [(BID, 15), (BID + 1, 15)]
