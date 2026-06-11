"""Escopo de Lojas por equipe (user_scope, Fase 1).

Admin vê tudo. User sem equipe vê tudo. User com equipe vê SÓ as lojas
cuja sales_team está na lista user.sales_teams.

GET /api/pricing/store-info passa user_scope(StoreInfo, user) na where
clause — esses 3 cenários cobrem o filtro indireto via API.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import StoreInfo, User, UserRole, UserStatus

PERM = {"tabela_precos": {"view": True, "edit": True, "delete": False}}


async def _seed_user(
    db: AsyncSession, *, role: UserRole, sales_teams: list[int] | None,
) -> User:
    email = f"st-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(
        open_id=f"email:{email}", email=email,
        role=role, status=UserStatus.ACTIVE,
        permissions=PERM, sales_teams=sales_teams,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _seed_lojas(db: AsyncSession, owner: User) -> dict[str, str]:
    """3 lojas: t1 (equipe 1), t2 (equipe 2), tn (sem equipe)."""
    out: dict[str, str] = {}
    for key, team in [("t1", 1), ("t2", 2), ("tn", None)]:
        s = StoreInfo(
            user_id=owner.id, platform="ml",
            account_name=f"loja-{key}", sales_team=team,
        )
        db.add(s)
        await db.flush()
        out[key] = str(s.id)
    await db.commit()
    return out


@pytest_asyncio.fixture
async def owner(db: AsyncSession) -> User:
    """User dono das StoreInfo seedadas (FK user_id em store_info)."""
    return await _seed_user(db, role=UserRole.ADMIN, sales_teams=None)


@pytest.mark.asyncio
async def test_admin_ve_todas_as_lojas(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], owner: User,
):
    ids = await _seed_lojas(db, owner)
    admin = await _seed_user(db, role=UserRole.ADMIN, sales_teams=None)
    auth_as(admin)

    r = await client.get("/api/pricing/store-info")
    assert r.status_code == 200
    got = {row["id"] for row in r.json()}
    assert ids["t1"] in got and ids["t2"] in got and ids["tn"] in got


@pytest.mark.asyncio
async def test_user_com_equipe_ve_so_lojas_da_equipe(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], owner: User,
):
    ids = await _seed_lojas(db, owner)
    user = await _seed_user(db, role=UserRole.USER, sales_teams=[1])
    auth_as(user)

    r = await client.get("/api/pricing/store-info")
    assert r.status_code == 200
    got = {row["id"] for row in r.json()}
    assert got == {ids["t1"]}


@pytest.mark.asyncio
async def test_user_com_multiplas_equipes_ve_uniao(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], owner: User,
):
    ids = await _seed_lojas(db, owner)
    user = await _seed_user(db, role=UserRole.USER, sales_teams=[1, 2])
    auth_as(user)

    r = await client.get("/api/pricing/store-info")
    assert r.status_code == 200
    got = {row["id"] for row in r.json()}
    assert got == {ids["t1"], ids["t2"]}  # tn fica fora (sales_team=null)


@pytest.mark.asyncio
async def test_user_sem_equipe_ve_todas_lojas(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], owner: User,
):
    """Comportamento atual preservado: user sem sales_teams vê tudo."""
    ids = await _seed_lojas(db, owner)
    user = await _seed_user(db, role=UserRole.USER, sales_teams=None)
    auth_as(user)

    r = await client.get("/api/pricing/store-info")
    assert r.status_code == 200
    got = {row["id"] for row in r.json()}
    assert ids["t1"] in got and ids["t2"] in got and ids["tn"] in got
