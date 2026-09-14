"""Amazon invoicing must not create a physical shipment through Bling ingest."""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.models import BlingOrder, IntegrationPlatform, MargemAudit, User, UserRole
from app.routers.estoque import list_estoque_pedidos
from app.services import amazon_bling_shipment as guard
from app.services import bling_orders as bo


@pytest_asyncio.fixture(autouse=True)
async def unique_order_index(db):
    await db.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_bling_orders_bling_id_item_index "
            "ON bling_orders (bling_id, item_index)"
        )
    )
    await db.commit()


def raw_order():
    return {
        "id": 26854999840,
        "numero": "296709",
        "numeroLoja": "701-2009995-0625021",
        "data": datetime.now(UTC).date().isoformat(),
        "loja": {"id": 204713113},
        "situacao": {"id": 15},
        "itens": [{"id": 1, "codigo": "uaf001m1.110", "quantidade": 1, "valor": 10}],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["insert", "same_items", "changed_items"])
@pytest.mark.parametrize(
    "status",
    [
        {"order_status": "Shipped", "easyship_status": "PendingDropOff"},
        {"order_status": "Shipped", "easyship_status": None},
        None,
    ],
)
async def test_bling_15_without_collection_stays_pending(db, monkeypatch, path, status):
    raw = raw_order()
    if path != "insert":
        old = {**raw, "situacao": {"id": 6}}
        if path == "changed_items":
            old = {**old, "itens": [{**raw["itens"][0], "codigo": "old-sku"}]}
        await bo.upsert_order(db, old)
        await db.commit()
    integration = SimpleNamespace(platform=IntegrationPlatform.AMAZON, credentials="unused")
    monkeypatch.setattr(
        guard,
        "_resolve_store_and_integration",
        AsyncMock(
            return_value=(
                SimpleNamespace(marketplace=SimpleNamespace(value="amazon")),
                integration,
            )
        ),
    )
    monkeypatch.setattr(guard, "decrypt_json", lambda _: {})
    monkeypatch.setattr(
        guard,
        "AmazonClient",
        lambda *args, **kwargs: SimpleNamespace(
            get_order_status=AsyncMock(return_value=status),
        ),
    )
    await bo.upsert_order(db, raw)
    await db.commit()
    db.expire_all()
    order = (await db.execute(select(BlingOrder))).scalar_one()
    assert order.situacao == "21"
    assert order.em_andamento_data is None
    assert order.item_codigo == "uaf001m1.110"
    assert raw["situacao"]["id"] == 15  # input stays intact
    audit = (await db.execute(select(MargemAudit))).scalar_one()
    assert audit.origem == "amazon_envio_validacao"
    # Test the operator-facing result as well as the persisted situation.
    today = datetime.now(UTC).date()
    response = await list_estoque_pedidos(
        db,
        User(role=UserRole.ADMIN),
        today,
        today,
        "nao_enviado",
        None,
    )
    assert any(
        r["pedido_bling"] == "296709" and r["status"] == "nao_enviado" and r["enviado_em"] is None
        for r in response["data"]
    )


@pytest.mark.asyncio
async def test_confirmed_pickup_is_accepted(db, monkeypatch):
    monkeypatch.setattr(bo, "amazon_bling_dispatch_blocked", AsyncMock(return_value=False))
    await bo.upsert_order(db, raw_order())
    await db.commit()
    order = (await db.execute(select(BlingOrder))).scalar_one()
    assert order.situacao == "15"
    assert order.em_andamento_data is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("previous", ["15", "83953", "83957"])
async def test_existing_shipped_history_is_not_regressed(db, monkeypatch, previous):
    raw = raw_order()
    db.add(
        BlingOrder(
            bling_id=raw["id"],
            numero=raw["numero"],
            item_index=0,
            situacao=previous,
            item_codigo="old",
            em_andamento_data=date(2026, 8, 1),
        )
    )
    await db.commit()
    check = AsyncMock(side_effect=AssertionError("must not recheck shipped history"))
    monkeypatch.setattr(bo, "amazon_bling_dispatch_blocked", check)
    await bo.upsert_order(db, raw)
    await db.commit()
    db.expire_all()
    order = (await db.execute(select(BlingOrder))).scalar_one()
    assert order.situacao == "15"
    assert order.em_andamento_data == date(2026, 8, 1)
    check.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", [None, "ml", "shopee"])
async def test_other_marketplaces_keep_bling_situation(db, monkeypatch, platform):
    store = SimpleNamespace(marketplace=SimpleNamespace(value=platform)) if platform else None
    monkeypatch.setattr(
        guard, "_resolve_store_and_integration", AsyncMock(return_value=(store, None))
    )
    assert not await guard.amazon_bling_dispatch_blocked(db, loja="1", order_id="order")


@pytest.mark.asyncio
async def test_amazon_without_integration_or_response_waits(db, monkeypatch):
    store = SimpleNamespace(marketplace=SimpleNamespace(value="amazon"))
    monkeypatch.setattr(
        guard, "_resolve_store_and_integration", AsyncMock(return_value=(store, None))
    )
    assert await guard.amazon_bling_dispatch_blocked(db, loja="1", order_id="order")
    integration = SimpleNamespace(platform=IntegrationPlatform.AMAZON, credentials="unused")
    monkeypatch.setattr(
        guard, "_resolve_store_and_integration", AsyncMock(return_value=(store, integration))
    )
    monkeypatch.setattr(guard, "decrypt_json", lambda _: {})
    monkeypatch.setattr(
        guard,
        "AmazonClient",
        lambda *args, **kwargs: SimpleNamespace(
            get_order_status=AsyncMock(side_effect=TimeoutError),
        ),
    )
    assert await guard.amazon_bling_dispatch_blocked(db, loja="1", order_id="order")
