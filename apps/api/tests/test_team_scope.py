"""Escopo por equipe de vendas (Fase 2).

Usuário não-admin COM equipe(s) só enxerga os dados das lojas da sua equipe;
admin e usuário SEM equipe permanecem irrestritos (veem tudo). Cobre o
resolver central (`resolve_team_scope`) e a aplicação em Integrações, Anúncios,
Reembolso e Logística.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.team_scope import resolve_team_scope
from app.models import (
    BlingOrder,
    Integration,
    IntegrationPlatform,
    Listing,
    ListingStatus,
    Logistica,
    Refund,
    StoreInfo,
    User,
    UserRole,
    UserStatus,
)

PERM = {
    "integracoes": {"view": True, "edit": True},
    "anuncios": {"view": True, "edit": True},
    "reembolso": {"view": True, "edit": True},
    "logistica": {"view": True, "edit": True},
}


def _mk_user(role: UserRole, teams: list[int] | None) -> User:
    email = f"team-{uuid.uuid4().hex[:6]}@davinci-test.com"
    return User(
        open_id=f"email:{email}",
        email=email,
        role=role,
        status=UserStatus.ACTIVE,
        permissions=PERM,
        sales_teams=teams,
    )


@pytest_asyncio.fixture
async def scope_setup(db: AsyncSession) -> dict:
    """Duas lojas: uma da equipe 1, outra da equipe 2. Cada uma com integração,
    anúncio, reembolso e logística próprios."""
    owner = _mk_user(UserRole.ADMIN, None)
    db.add(owner)
    await db.flush()

    def _seed(team: int, tag: str) -> dict:
        integ = Integration(
            user_id=owner.id,
            platform=IntegrationPlatform.SHOPEE,
            name=f"conta-{tag}",
            credentials=b"x",
        )
        db.add(integ)
        return {"integ": integ, "team": team, "tag": tag}

    a = _seed(1, "alpha")
    b = _seed(2, "beta")
    await db.flush()

    for s in (a, b):
        info = StoreInfo(
            user_id=owner.id,
            platform="shopee",
            account_name=f"conta-{s['tag']}",
            integration_id=s["integ"].id,
            bling_store_id=f"loja{s['team']}",
            sales_team=s["team"],
        )
        db.add(info)
        db.add(
            Listing(
                user_id=owner.id,
                integration_id=s["integ"].id,
                platform=IntegrationPlatform.SHOPEE,
                external_id=f"ext-{s['tag']}",
                title=f"anuncio {s['tag']}",
                status=ListingStatus.ACTIVE,
            )
        )
        db.add(BlingOrder(numero=f"ped{s['team']}", loja=f"loja{s['team']}"))
        db.add(
            Refund(
                pedido_bling=f"ped{s['team']}",
                conta=f"conta-{s['tag']}",
                plataforma="shopee",
            )
        )
        db.add(
            Logistica(
                pedido_bling=f"ped{s['team']}",
                conta=f"conta-{s['tag']}",
                plataforma="Shopee",
            )
        )
    await db.commit()
    return {
        "integ_alpha": a["integ"].id,
        "integ_beta": b["integ"].id,
    }


@pytest.mark.asyncio
async def test_resolver_admin_and_no_team_unrestricted(
    db: AsyncSession, scope_setup: dict
):
    admin = _mk_user(UserRole.ADMIN, [1])
    no_team = _mk_user(UserRole.USER, None)
    for u in (admin, no_team):
        scope = await resolve_team_scope(db, u)
        assert scope.unrestricted is True


@pytest.mark.asyncio
async def test_resolver_team_user_restricted(db: AsyncSession, scope_setup: dict):
    u = _mk_user(UserRole.USER, [1])
    scope = await resolve_team_scope(db, u)
    assert scope.unrestricted is False
    assert scope_setup["integ_alpha"] in scope.integration_ids
    assert scope_setup["integ_beta"] not in scope.integration_ids
    assert scope.bling_store_ids == {"loja1"}
    assert scope.account_names == {"conta-alpha"}


@pytest.mark.asyncio
async def test_integrations_and_listings_scoped(
    db: AsyncSession,
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
    scope_setup: dict,
):
    u = _mk_user(UserRole.USER, [1])
    db.add(u)
    await db.commit()
    auth_as(u)

    r = await client.get("/api/integrations")
    ids = {i["id"] for i in r.json()}
    assert str(scope_setup["integ_alpha"]) in ids
    assert str(scope_setup["integ_beta"]) not in ids

    r = await client.get("/api/listings")
    titles = {i["title"] for i in r.json()["items"]}
    assert "anuncio alpha" in titles
    assert "anuncio beta" not in titles


@pytest.mark.asyncio
async def test_refunds_and_logistica_scoped(
    db: AsyncSession,
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
    scope_setup: dict,
):
    u = _mk_user(UserRole.USER, [1])
    db.add(u)
    await db.commit()
    auth_as(u)

    r = await client.get("/api/refunds")
    contas = {i["conta"] for i in r.json()["items"]}
    assert "conta-alpha" in contas
    assert "conta-beta" not in contas

    r = await client.get("/api/logistica")
    pedidos = {i["pedido_bling"] for i in r.json()}
    assert "ped1" in pedidos
    assert "ped2" not in pedidos


@pytest.mark.asyncio
async def test_admin_sees_both(
    db: AsyncSession,
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
    scope_setup: dict,
):
    admin = _mk_user(UserRole.ADMIN, None)
    db.add(admin)
    await db.commit()
    auth_as(admin)

    r = await client.get("/api/integrations")
    ids = {i["id"] for i in r.json()}
    assert str(scope_setup["integ_alpha"]) in ids
    assert str(scope_setup["integ_beta"]) in ids
