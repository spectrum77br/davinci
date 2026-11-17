from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import Integration, IntegrationPlatform, Product, ProductLink, SyncLog
from app.security.cipher import encrypt_json
from app.services.carrefour_stock_source import confirmed_balance, refresh_carrefour_stock_target
from app.services.marketplaces.bling import BlingClient
from app.services.sync_orchestrator import SyncOrchestrator


@pytest.mark.parametrize(
    "value,expected",
    [
        (12, 12),
        ("12.0", 12),
        (0, 0),
        (-3, 0),
        (None, None),
        (True, None),
        ("NaN", None),
        ("Infinity", None),
        (1.5, None),
    ],
)
def test_confirmed_source_never_converts_unknown_to_zero(value, expected):
    raw = {"id": 10, "codigo": "Exact.1", "situacao": "A", "estoque": {"saldoVirtualTotal": value}}
    assert confirmed_balance(raw, sku="Exact.1", external_id="10") == expected
    assert confirmed_balance(raw, sku="other", external_id="10") is None
    assert confirmed_balance(raw, sku="Exact.1", external_id="11") is None
    raw["situacao"] = "E"
    assert confirmed_balance(raw, sku="Exact.1", external_id="10") is None


async def test_refresh_uses_unique_bling_source_without_marketplace_writes(
    db, make_user, monkeypatch
):
    user = await make_user()
    bling = Integration(
        user_id=user.id,
        platform=IntegrationPlatform.BLING,
        name="Bling",
        credentials=encrypt_json({}),
        status="active",
    )
    carrefour = Integration(
        user_id=user.id,
        platform=IntegrationPlatform.CARREFOUR,
        name="Poofy",
        credentials=encrypt_json({}),
        status="active",
    )
    db.add_all([bling, carrefour])
    await db.flush()
    product = Product(user_id=user.id, sku="exact-1", name="Test", stock=999, situacao="A")
    db.add(product)
    await db.flush()
    source = ProductLink(
        user_id=user.id,
        product_id=product.id,
        integration_id=bling.id,
        platform=IntegrationPlatform.BLING,
        external_id="10",
        external_sku="exact-1",
    )
    target = ProductLink(
        user_id=user.id,
        product_id=product.id,
        integration_id=carrefour.id,
        platform=IntegrationPlatform.CARREFOUR,
        external_id="123",
        external_sku="exact-1",
    )
    db.add_all([source, target])
    await db.commit()
    requests = []

    async def fake_get(self, ident):
        requests.append(ident)
        return {
            "id": 10,
            "codigo": "exact-1",
            "situacao": "A",
            "estoque": {"saldoVirtualTotal": 14},
        }

    async def fake_client(self, integration):
        assert integration.id == bling.id
        return BlingClient({})

    monkeypatch.setattr(BlingClient, "get_product", fake_get)
    monkeypatch.setattr(SyncOrchestrator, "_client", fake_client)
    assert await refresh_carrefour_stock_target(target.id) == 14
    assert requests == [10]
    await db.refresh(product)
    assert product.stock == 14
    logs = list((await db.scalars(select(SyncLog).where(SyncLog.product_id == product.id))).all())
    assert len(logs) == 1 and logs[0].qty_after == 14
    # Duplicated origin is ambiguous and cannot publish stale local 14.
    db.add(
        ProductLink(
            user_id=user.id,
            product_id=product.id,
            integration_id=bling.id,
            platform=IntegrationPlatform.BLING,
            external_id="11",
            external_sku="exact-1",
        )
    )
    await db.commit()
    assert await refresh_carrefour_stock_target(target.id) is None
    assert requests == [10]
    assert await refresh_carrefour_stock_target(uuid4()) is None
