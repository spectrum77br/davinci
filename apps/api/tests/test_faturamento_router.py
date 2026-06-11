"""Aba Faturamento — endpoint GET /api/faturamento.

Cobre:
  * Admin vê todas as lojas; user com sales_teams=[X] vê só lojas com
    StoreInfo.sales_team=X.
  * Dedup por bling_id: pedido com 2 itens conta como 1 pedido e usa
    max(total) (não a soma das linhas).
  * situacao != '83953' é ignorado e o filtro de período funciona.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingOrder,
    Company,
    Marketplace,
    Store,
    StoreInfo,
    StoreStatus,
    User,
    UserRole,
    UserStatus,
)

PERM = {"faturamento": {"view": True, "edit": False, "delete": False}}


async def _seed_user(
    db: AsyncSession, *, role: UserRole, sales_teams: list[int] | None,
) -> User:
    email = f"ft-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(
        open_id=f"email:{email}", email=email,
        role=role, status=UserStatus.ACTIVE,
        permissions=PERM, sales_teams=sales_teams,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _seed_lojas(db: AsyncSession, owner: User) -> dict[str, dict]:
    """Cria 2 lojas (ml/shopee) + companies + StoreInfo com sales_team."""
    co = Company(razao_social="C", apelido="c")
    db.add(co)
    await db.flush()
    out: dict[str, dict] = {}
    plan = [
        ("ml", 1001, "Conta ML", "ml", 1),
        ("shopee", 1002, "Conta Shopee", "shopee", 2),
    ]
    for key, bling_id, account, plat, team in plan:
        s = Store(
            company_id=co.id,
            marketplace=Marketplace(key),
            bling_store_id=bling_id,
            status=StoreStatus.ACTIVE,
        )
        db.add(s)
        await db.flush()
        si = StoreInfo(
            user_id=owner.id, platform=plat,
            account_name=account,
            bling_store_id=str(bling_id),
            sales_team=team,
        )
        db.add(si)
        await db.flush()
        out[key] = {"store_id": str(s.id), "bling_store_id": bling_id, "team": team}
    await db.commit()
    return out


async def _seed_pedido(
    db: AsyncSession, *, bling_id: int, store_id, total: Decimal,
    data: datetime, situacao: str = "83953", n_itens: int = 1,
) -> None:
    """Cria 1 pedido com `n_itens` linhas em bling_orders (uma por item).
    `total` se repete em cada linha — espelha o cenário de prod."""
    for i in range(n_itens):
        db.add(BlingOrder(
            bling_id=bling_id, numero=str(bling_id),
            item_codigo=f"sku-{bling_id}-{i}", item_index=i,
            situacao=situacao, total=total, data=data,
            store_id=store_id,
        ))
    await db.commit()


_HOJE = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)
_PERIODO = (
    (_HOJE - timedelta(days=30)).isoformat(),
    (_HOJE + timedelta(days=1)).isoformat(),
)


@pytest_asyncio.fixture
async def owner(db: AsyncSession) -> User:
    return await _seed_user(db, role=UserRole.ADMIN, sales_teams=None)


@pytest.mark.asyncio
async def test_admin_ve_todas_as_lojas(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], owner: User,
):
    lojas = await _seed_lojas(db, owner)
    await _seed_pedido(
        db, bling_id=9001, store_id=lojas["ml"]["store_id"],
        total=Decimal("100.00"), data=_HOJE,
    )
    await _seed_pedido(
        db, bling_id=9002, store_id=lojas["shopee"]["store_id"],
        total=Decimal("250.00"), data=_HOJE,
    )
    admin = await _seed_user(db, role=UserRole.ADMIN, sales_teams=None)
    auth_as(admin)

    r = await client.get(
        "/api/faturamento",
        params={"start": _PERIODO[0], "end": _PERIODO[1]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    store_ids = {it["store_id"] for it in body["itens"]}
    assert lojas["ml"]["store_id"] in store_ids
    assert lojas["shopee"]["store_id"] in store_ids
    assert body["total_pedidos"] == 2
    assert Decimal(str(body["total_faturamento"])) == Decimal("350.00")


@pytest.mark.asyncio
async def test_user_com_equipe_ve_so_lojas_da_equipe(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], owner: User,
):
    lojas = await _seed_lojas(db, owner)
    await _seed_pedido(
        db, bling_id=9011, store_id=lojas["ml"]["store_id"],
        total=Decimal("100.00"), data=_HOJE,
    )
    await _seed_pedido(
        db, bling_id=9012, store_id=lojas["shopee"]["store_id"],
        total=Decimal("250.00"), data=_HOJE,
    )
    user = await _seed_user(db, role=UserRole.USER, sales_teams=[1])
    auth_as(user)

    r = await client.get(
        "/api/faturamento",
        params={"start": _PERIODO[0], "end": _PERIODO[1]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["itens"]) == 1
    assert body["itens"][0]["store_id"] == lojas["ml"]["store_id"]
    assert body["total_pedidos"] == 1
    assert Decimal(str(body["total_faturamento"])) == Decimal("100.00")


@pytest.mark.asyncio
async def test_dedup_por_bling_id_usa_max_total(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], owner: User,
):
    """Pedido com 2 itens → 1 pedido, total = max(total) (não soma).
    bling_orders.total repete em cada linha do mesmo bling_id."""
    lojas = await _seed_lojas(db, owner)
    await _seed_pedido(
        db, bling_id=9021, store_id=lojas["ml"]["store_id"],
        total=Decimal("150.00"), data=_HOJE, n_itens=3,
    )
    admin = await _seed_user(db, role=UserRole.ADMIN, sales_teams=None)
    auth_as(admin)

    r = await client.get(
        "/api/faturamento",
        params={"start": _PERIODO[0], "end": _PERIODO[1]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_pedidos"] == 1
    assert Decimal(str(body["total_faturamento"])) == Decimal("150.00")


@pytest.mark.asyncio
async def test_ignora_outras_situacoes_e_fora_do_periodo(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], owner: User,
):
    lojas = await _seed_lojas(db, owner)
    # Entregue, dentro do período → conta.
    await _seed_pedido(
        db, bling_id=9031, store_id=lojas["ml"]["store_id"],
        total=Decimal("100.00"), data=_HOJE,
    )
    # Outras situações no mesmo período → não contam.
    await _seed_pedido(
        db, bling_id=9032, store_id=lojas["ml"]["store_id"],
        total=Decimal("999.99"), data=_HOJE, situacao="15",
    )
    await _seed_pedido(
        db, bling_id=9033, store_id=lojas["ml"]["store_id"],
        total=Decimal("777.00"), data=_HOJE, situacao="12",
    )
    # Entregue mas fora do período → não conta.
    await _seed_pedido(
        db, bling_id=9034, store_id=lojas["ml"]["store_id"],
        total=Decimal("500.00"), data=_HOJE - timedelta(days=120),
    )
    admin = await _seed_user(db, role=UserRole.ADMIN, sales_teams=None)
    auth_as(admin)

    r = await client.get(
        "/api/faturamento",
        params={"start": _PERIODO[0], "end": _PERIODO[1]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_pedidos"] == 1
    assert Decimal(str(body["total_faturamento"])) == Decimal("100.00")
