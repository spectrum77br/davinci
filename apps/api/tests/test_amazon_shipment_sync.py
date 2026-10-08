"""Physical Amazon shipment confirmation with real DB and isolated fake APIs."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingEnvioEvento, BlingOrder, IntegrationPlatform, Logistica, MargemAudit
from app.services import amazon_shipment_sync as svc
from app.services import marketplace_shipment_check as sweep
from app.services.logistica_track import EventoRastreio

BID = 99880101
NUMBER = "301695"
MP = "701-1111111-2222222"
STORE = "456"
TRACK = "AD123456789BR"
OTHER_TRACK = "AD987654321BR"
ORDER_AT = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=4)
POSTED = ORDER_AT + timedelta(days=1, hours=3)
LAST_READ = ORDER_AT + timedelta(days=2)


def event(description="Objeto em transferência", when=POSTED):
    return EventoRastreio(quando=when, descricao=description, local="SP")


async def make_order(db, *, bid=BID, numero=NUMBER, mp=MP, count=1, **overrides):
    base = {
        "bling_id": bid,
        "numero": numero,
        "numeroloja": mp,
        "loja": STORE,
        "data": ORDER_AT,
        "situacao": "21",
        "em_andamento_data": ORDER_AT.date(),
        "created_at": ORDER_AT,
    }
    base.update(overrides)
    rows = [
        BlingOrder(**base, item_index=i, item_codigo=f"sku-{i}", item_quantidade=1)
        for i in range(count)
    ]
    db.add_all(rows)
    await db.commit()
    return rows


async def make_logistics(db, *, numero=NUMBER, mp=MP, track=TRACK, **overrides):
    values = {
        "pedido_bling": numero,
        "pedido_marketplace": mp,
        "plataforma": "Amazon",
        "conta": "kia",
        "data": ORDER_AT.date(),
        "amazon_canal": "proprio",
        "servico_envio": "SEDEX",
        "rastreio": track,
        "rastreio_17track": track,
        "rastreio_17track_at": ORDER_AT,
        "localizacao": "SP — Objeto em transferência",
        "localizacao_at": LAST_READ,
        "status_bling": "Em digitação",
    }
    values.update(overrides)
    row = Logistica(**values)
    db.add(row)
    await db.commit()
    return row


@pytest.fixture
def fake_tracking(monkeypatch):
    query = AsyncMock(return_value={TRACK: [event()]})
    monkeypatch.setattr(svc.logistica_track, "eventos", query)
    return query


class Bling:
    def __init__(
        self, *, before=21, after=15, patch_error=None, overrides=None, read_error=None, track=TRACK
    ):
        self.before, self.after = before, after
        self.patch_error, self.read_error = patch_error, read_error
        self.overrides = overrides or {}
        self.track = track
        self.reads = 0
        self.writes = []

    async def _request(self, method, path):
        assert method == "GET" and path == f"/pedidos/vendas/{BID}"
        if self.read_error:
            raise self.read_error
        state = self.before if self.reads == 0 else self.after
        self.reads += 1
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": BID,
                    "numero": int(NUMBER),
                    "numeroLoja": MP,
                    "loja": {"id": int(STORE)},
                    "situacao": {"id": state},
                    "transporte": {
                        "volumes": [{"codigoRastreamento": self.track, "servico": "SEDEX"}]
                    },
                    **self.overrides,
                }
            },
        )

    async def update_order_situacao(self, bid, state):
        self.writes.append((bid, state))
        if self.patch_error:
            req = httpx.Request("PATCH", "https://bling.test/order/status")
            raise httpx.HTTPStatusError(
                "rejected", request=req, response=httpx.Response(self.patch_error, request=req)
            )
        return True


async def ledger(db):
    return (await db.scalars(select(BlingEnvioEvento))).all()


async def correction_inputs(db, fake_tracking, **order_kw):
    rows = await make_order(db, **order_kw)
    await make_logistics(db)
    confirmations = await svc.load_amazon_correios_confirmations(db, [rows[0]])
    assert BID in confirmations
    return rows, confirmations[BID]


@pytest.mark.asyncio
@pytest.mark.parametrize("numero", ["301695", "301840", "301850", "301869"])
async def test_reported_four_cases_require_current_physical_tracking(db, fake_tracking, numero):
    rows = await make_order(db, numero=numero)
    log = await make_logistics(db, numero=numero)
    found = await svc.load_amazon_correios_confirmations(db, rows)
    assert set(found) == {BID}
    assert found[BID].shipped_at == POSTED
    assert found[BID].tracking_numbers == (TRACK,)
    assert log.id in found[BID].logistics_ids
    fake_tracking.assert_awaited_once()
    assert rows[0].situacao == "21" and not await ledger(db)  # detection is read-only


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "description",
    [
        "Objeto postado",
        "Objeto em transferência",
        "Objeto em trânsito",
        "Objeto saiu para entrega ao destinatário",
        "Objeto entregue ao destinatário",
    ],
)
async def test_physical_correios_events_confirm(db, fake_tracking, description):
    rows = await make_order(db)
    await make_logistics(db, localizacao="SP — " + description)
    fake_tracking.return_value = {TRACK: [event(description)]}
    assert BID in await svc.load_amazon_correios_confirmations(db, rows)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "description",
    [
        "Pré-postagem",
        "Objeto não postado",
        "Aguardando postagem",
        "Etiqueta emitida",
        "Aguardando coleta",
        "Dados enviados eletronicamente",
        "Objeto aguardando postagem pelo remetente",
        "",
        "Status desconhecido",
    ],
)
async def test_label_pending_unknown_and_empty_events_never_confirm(db, fake_tracking, description):
    rows = await make_order(db)
    await make_logistics(db)
    fake_tracking.return_value = {TRACK: [event(description)]}
    assert await svc.load_amazon_correios_confirmations(db, rows) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {TRACK: []}, {OTHER_TRACK: [event()]}])
async def test_missing_or_wrong_tracking_response_never_confirms(db, fake_tracking, payload):
    rows = await make_order(db)
    await make_logistics(db)
    fake_tracking.return_value = payload
    assert await svc.load_amazon_correios_confirmations(db, rows) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "when",
    [
        ORDER_AT - timedelta(days=1),
        ORDER_AT - timedelta(hours=1),
        datetime.now(UTC) + timedelta(days=1),
        POSTED.replace(tzinfo=None),
    ],
)
async def test_old_future_or_undated_physical_event_is_not_accepted(db, fake_tracking, when):
    rows = await make_order(db)
    await make_logistics(db)
    fake_tracking.return_value = {TRACK: [event(when=when)]}
    assert await svc.load_amazon_correios_confirmations(db, rows) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"pedido_bling": "999999"},
        {"pedido_marketplace": "701-9999999-9999999"},
        {"plataforma": "Shopee"},
        {"amazon_canal": "dba"},
        {"amazon_canal": None},
        {"servico_envio": "Logistica Amazon Dba"},
        {"rastreio": OTHER_TRACK},
        {"rastreio_17track": None},
        {"rastreio": "INVALIDBR", "rastreio_17track": "INVALIDBR"},
        {"localizacao_at": None},
        {"localizacao_at": ORDER_AT - timedelta(days=1)},
        {"localizacao_at": datetime.now(UTC) + timedelta(days=1)},
        {"localizacao": "Aguardando postagem"},
        {"localizacao": ""},
    ],
)
async def test_stale_wrong_identity_or_nonphysical_local_record_is_not_evidence(
    db, fake_tracking, overrides
):
    rows = await make_order(db)
    await make_logistics(db, **overrides)
    assert await svc.load_amazon_correios_confirmations(db, rows) == {}
    fake_tracking.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [403, 429, 500])
async def test_tracking_api_failure_never_reuses_local_location(db, fake_tracking, code):
    rows = await make_order(db)
    await make_logistics(db)
    req = httpx.Request("POST", "https://tracking.test/gettrackinfo")
    fake_tracking.side_effect = httpx.HTTPStatusError(
        "unavailable", request=req, response=httpx.Response(code, request=req)
    )
    assert await svc.load_amazon_correios_confirmations(db, rows) == {}
    assert rows[0].situacao == "21" and not await ledger(db)


@pytest.mark.asyncio
async def test_same_tracking_attached_to_two_orders_never_promotes_either(db, fake_tracking):
    one = await make_order(db)
    two = await make_order(db, bid=BID + 1, numero="301840", mp="701-3333333-4444444")
    await make_logistics(db)
    await make_logistics(db, numero="301840", mp="701-3333333-4444444")
    assert await svc.load_amazon_correios_confirmations(db, one + two) == {}


@pytest.mark.asyncio
async def test_confirmed_shipment_patches_bling_first_then_all_items_and_is_idempotent(
    db, fake_tracking
):
    rows, confirmation = await correction_inputs(db, fake_tracking, count=2)
    bling = Bling()
    before = datetime.now(UTC)
    applied = await svc.apply_amazon_shipment(db, bling, rows[0], confirmation)
    await db.commit()
    assert applied.local_updated == 2 and applied.bling_updated and not applied.error
    assert bling.writes == [(BID, 15)] and bling.reads == 2
    for row in rows:
        await db.refresh(row)
        assert row.situacao == "15" and row.em_andamento_data == ORDER_AT.date()
    events = await ledger(db)
    assert len(events) == 2 and all(e.occurred_at >= before - timedelta(seconds=1) for e in events)
    assert len((await db.scalars(select(MargemAudit))).all()) >= 1
    again = await svc.apply_amazon_shipment(db, bling, rows[0], confirmation)
    assert again.local_updated == 0 and bling.writes == [(BID, 15)]
    assert len(await ledger(db)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [400, 409, 422, 500])
async def test_bling_patch_failure_with_no_remote_transition_never_marks_local_sent(
    db, fake_tracking, code
):
    rows, confirmation = await correction_inputs(db, fake_tracking)
    applied = await svc.apply_amazon_shipment(
        db, Bling(after=21, patch_error=code), rows[0], confirmation
    )
    assert applied.error and applied.local_updated == 0
    await db.refresh(rows[0])
    assert rows[0].situacao == "21" and not await ledger(db)
    assert not (await db.scalars(select(MargemAudit))).all()


@pytest.mark.asyncio
async def test_patch_success_without_readback_confirmation_stays_not_sent(db, fake_tracking):
    rows, confirmation = await correction_inputs(db, fake_tracking)
    result = await svc.apply_amazon_shipment(db, Bling(after=21), rows[0], confirmation)
    assert result.error and result.local_updated == 0 and not await ledger(db)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"numeroLoja": "701-9999999-9999999"},
        {"loja": {"id": 789}},
        {"id": BID + 1},
        {"numero": 999999},
    ],
)
async def test_bling_identity_or_store_mismatch_never_patches(db, fake_tracking, overrides):
    rows, confirmation = await correction_inputs(db, fake_tracking)
    bling = Bling(overrides=overrides)
    result = await svc.apply_amazon_shipment(db, bling, rows[0], confirmation)
    assert result.local_updated == 0 and not bling.writes and not await ledger(db)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "volumes",
    [
        [],
        [{"codigoRastreamento": OTHER_TRACK, "servico": "SEDEX"}],
        [{"codigoRastreamento": TRACK, "servico": "Logistica Amazon Dba"}],
        [
            {"codigoRastreamento": TRACK, "servico": "SEDEX"},
            {"codigoRastreamento": "", "servico": "SEDEX"},
        ],
        [
            {"codigoRastreamento": TRACK, "servico": "SEDEX"},
            {"codigoRastreamento": OTHER_TRACK, "servico": "SEDEX"},
        ],
    ],
)
async def test_changed_dba_or_partially_confirmed_volumes_never_patches(db, fake_tracking, volumes):
    rows, confirmation = await correction_inputs(db, fake_tracking)
    bling = Bling(overrides={"transporte": {"volumes": volumes}})
    result = await svc.apply_amazon_shipment(db, bling, rows[0], confirmation)
    assert result.local_updated == 0 and not bling.writes and not await ledger(db)


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_state", [12, 9, 83957, 83953])
async def test_cancelled_or_advanced_bling_state_is_never_overwritten(
    db, fake_tracking, remote_state
):
    rows, confirmation = await correction_inputs(db, fake_tracking)
    bling = Bling(before=remote_state)
    result = await svc.apply_amazon_shipment(db, bling, rows[0], confirmation)
    assert result.local_updated == 0 and not bling.writes and not await ledger(db)


@pytest.mark.asyncio
async def test_local_cancellation_between_detection_and_apply_is_respected(db, fake_tracking):
    rows, confirmation = await correction_inputs(db, fake_tracking)
    async with AsyncSession(bind=db.bind) as other_session:
        await other_session.execute(
            update(BlingOrder).where(BlingOrder.bling_id == BID).values(situacao="12")
        )
        await other_session.commit()
    assert rows[0].situacao == "21"  # main-session candidate is deliberately stale
    bling = Bling()
    result = await svc.apply_amazon_shipment(db, bling, rows[0], confirmation)
    assert result.local_updated == 0 and not bling.writes and bling.reads == 0
    assert not await ledger(db)


@pytest.mark.asyncio
async def test_manual_return_to_digitacao_after_a_real_dispatch_is_not_undone(db, fake_tracking):
    rows, confirmation = await correction_inputs(db, fake_tracking)
    await svc.apply_amazon_shipment(db, Bling(), rows[0], confirmation)
    await db.commit()
    rows[0].situacao = "21"
    await db.commit()
    bling = Bling()
    result = await svc.apply_amazon_shipment(db, bling, rows[0], confirmation)
    assert result.local_updated == 0 and not bling.writes and bling.reads == 0
    assert len(await ledger(db)) == 1


@pytest.mark.asyncio
async def test_scheduled_sweep_routes_amazon_through_verified_apply(db, fake_tracking, monkeypatch):
    rows = await make_order(db)
    await make_logistics(db)
    integration = SimpleNamespace(
        id=uuid4(),
        platform=IntegrationPlatform.AMAZON,
        credentials=b"test",
        archived_at=None,
        status="active",
    )
    bling = Bling()
    amazon = SimpleNamespace(
        get_order_status=AsyncMock(
            return_value={
                "order_status": "Shipped",
                "fulfillment_channel": "MFN",
                "easyship_status": None,
                "last_update_date": LAST_READ.isoformat(),
            }
        )
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
    monkeypatch.setattr(sweep, "_build_bling_client", AsyncMock(return_value=bling))
    monkeypatch.setattr(sweep, "decrypt_json", lambda _: {})
    monkeypatch.setattr(sweep, "AmazonClient", lambda *args, **kwargs: amazon)
    finance = AsyncMock()
    monkeypatch.setattr(sweep, "_enfileirar_financeiro_amazon", finance)
    monkeypatch.setattr(sweep, "_registrar_resposta_da_loja", AsyncMock())
    monkeypatch.setattr(sweep, "_avisar_threema", AsyncMock())
    before = datetime.now(UTC)
    result = await sweep.run_check_marketplace_shipped_orders()
    assert result["local_updated"] == 1 and result["bling_updated"] == 1 and result["errors"] == 0
    assert len(await ledger(db)) == 1 and (await ledger(db))[0].occurred_at >= before - timedelta(
        seconds=1
    )
    finance.assert_awaited_once_with(BID)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "latest", ["Pré-postagem", "Etiqueta emitida", "Aguardando coleta", "Status desconhecido"]
)
async def test_latest_tracking_state_cannot_be_overruled_by_old_physical_event(
    db, fake_tracking, latest
):
    rows = await make_order(db)
    await make_logistics(db)
    fake_tracking.return_value = {TRACK: [event(latest, POSTED + timedelta(days=1)), event()]}
    assert await svc.load_amazon_correios_confirmations(db, rows) == {}


@pytest.mark.asyncio
async def test_logistics_tracking_changed_after_detection_prevents_bling_write(db, fake_tracking):
    rows, confirmation = await correction_inputs(db, fake_tracking)
    async with AsyncSession(bind=db.bind) as other_session:
        await other_session.execute(
            update(Logistica)
            .where(Logistica.id.in_(confirmation.logistics_ids))
            .values(
                rastreio=OTHER_TRACK,
                rastreio_17track=OTHER_TRACK,
            )
        )
        await other_session.commit()
    bling = Bling()
    result = await svc.apply_amazon_shipment(db, bling, rows[0], confirmation)
    assert result.local_updated == 0 and not bling.writes and bling.reads == 0
    assert not await ledger(db)


@pytest.mark.asyncio
async def test_all_current_volumes_need_a_physical_event_before_whole_order_is_sent(
    db, fake_tracking
):
    rows = await make_order(db)
    await make_logistics(db)
    await make_logistics(db, track=OTHER_TRACK)
    fake_tracking.return_value = {TRACK: [event()], OTHER_TRACK: [event("Pré-postagem")]}
    assert await svc.load_amazon_correios_confirmations(db, rows) == {}
    # All packages must be physically posted and match current Bling volumes.
    fake_tracking.return_value = {
        TRACK: [event()],
        OTHER_TRACK: [event(when=POSTED + timedelta(hours=1))],
    }
    found = await svc.load_amazon_correios_confirmations(db, rows)
    assert found[BID].shipped_at == POSTED + timedelta(hours=1)
    bling = Bling(
        overrides={
            "transporte": {
                "volumes": [
                    {"codigoRastreamento": TRACK, "servico": "SEDEX"},
                    {"codigoRastreamento": OTHER_TRACK, "servico": "SEDEX"},
                ]
            }
        }
    )
    result = await svc.apply_amazon_shipment(db, bling, rows[0], found[BID])
    assert result.local_updated == 1 and bling.writes == [(BID, 15)]


@pytest.mark.asyncio
async def test_native_amazon_physical_confirmation_keeps_verified_apply_without_correios(db):
    rows = await make_order(db)
    bling = Bling(overrides={"transporte": {"volumes": []}})
    result = await svc.apply_amazon_shipment(
        db,
        bling,
        rows[0],
        svc.AmazonShipmentConfirmation(shipped_at=POSTED),
    )
    assert result.local_updated == 1 and bling.writes == [(BID, 15)] and bling.reads == 2


@pytest.mark.asyncio
async def test_bling_read_timeout_never_changes_order(db, fake_tracking):
    rows, confirmation = await correction_inputs(db, fake_tracking)
    bling = Bling(read_error=httpx.ReadTimeout("timeout"))
    result = await svc.apply_amazon_shipment(db, bling, rows[0], confirmation)
    assert result.error and result.local_updated == 0 and not bling.writes and not await ledger(db)
