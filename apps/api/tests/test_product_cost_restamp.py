"""run_restamp_order_costs: re-carimba bling_orders.preco_custo NULL.

Regressão do bug "Item Custo = —" na página de margens: SKUs novos viram
pedido antes do sync diário preencher `products.bling_cost_price`, então a
linha entra com `preco_custo = NULL` e o caminho de re-ingestão "narrow
UPDATE" nunca re-carimba. O job re-carimba pedidos recentes cujo produto já
tem custo, sem tocar histórico antigo nem linhas sem produto.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, Product, User
from app.services.product_cost_sync import run_restamp_order_costs


@pytest_asyncio.fixture
async def user(make_user) -> User:
    return await make_user()


async def _add_product(
    db: AsyncSession, user: User, sku: str, cost: Decimal | None
) -> None:
    db.add(
        Product(
            user_id=user.id,
            sku=sku,
            name=sku,
            bling_cost_price=cost,
        )
    )
    await db.commit()


async def _add_order(
    db: AsyncSession,
    *,
    bling_id: int,
    item_codigo: str,
    preco_custo: float | None,
    age_days: int,
) -> None:
    db.add(
        BlingOrder(
            id=uuid4(),
            bling_id=bling_id,
            item_index=0,
            numero=f"PED-{bling_id}",
            item_codigo=item_codigo,
            preco_custo=preco_custo,
            created_at=datetime.now(UTC) - timedelta(days=age_days),
        )
    )
    await db.commit()


async def _custo(db: AsyncSession, bling_id: int) -> float | None:
    return (
        await db.execute(
            select(BlingOrder.preco_custo).where(BlingOrder.bling_id == bling_id)
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_restamp_fills_recent_null_with_product_cost(
    db: AsyncSession, user: User
):
    await _add_product(db, user, "dg090.sp", Decimal("1700.0000"))
    await _add_order(db, bling_id=1, item_codigo="dg090.sp", preco_custo=None, age_days=2)

    result = await run_restamp_order_costs(db)

    assert result["updated"] == 1
    assert await _custo(db, 1) == 1700.0


@pytest.mark.asyncio
async def test_restamp_skips_orders_outside_window(db: AsyncSession, user: User):
    await _add_product(db, user, "dg090.sp", Decimal("1700.0000"))
    await _add_order(db, bling_id=2, item_codigo="dg090.sp", preco_custo=None, age_days=120)

    result = await run_restamp_order_costs(db, window_days=60)

    assert result["updated"] == 0
    assert await _custo(db, 2) is None


@pytest.mark.asyncio
async def test_restamp_skips_orders_without_product_cost(db: AsyncSession, user: User):
    # SKU sem produto cadastrado (ex.: fake.pi) e SKU com produto mas custo NULL.
    await _add_product(db, user, "dg073.pi", None)
    await _add_order(db, bling_id=3, item_codigo="fake.pi", preco_custo=None, age_days=1)
    await _add_order(db, bling_id=4, item_codigo="dg073.pi", preco_custo=None, age_days=1)

    result = await run_restamp_order_costs(db)

    assert result["updated"] == 0
    assert await _custo(db, 3) is None
    assert await _custo(db, 4) is None


@pytest.mark.asyncio
async def test_restamp_does_not_overwrite_existing_cost(db: AsyncSession, user: User):
    await _add_product(db, user, "dg090.sp", Decimal("1700.0000"))
    await _add_order(db, bling_id=5, item_codigo="dg090.sp", preco_custo=999.0, age_days=1)

    result = await run_restamp_order_costs(db)

    assert result["updated"] == 0
    assert await _custo(db, 5) == 999.0
