"""Automações CRUD — catálogo manual das rotinas do sistema.

Aba Automações na tela de Integrações: registro editável (nome, o que faz,
frequência, categoria, se está funcionando). Gated pelo recurso `integracoes`
(view p/ listar, edit p/ criar/editar/remover). Admin sempre passa.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserRole, UserStatus


@pytest_asyncio.fixture
async def admin(db: AsyncSession) -> User:
    email = f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(
        open_id=f"email:{email}",
        email=email,
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def viewer(db: AsyncSession) -> User:
    """Usuário não-admin com view (mas sem edit) em integracoes."""
    email = f"vw-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(
        open_id=f"email:{email}",
        email=email,
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions={"integracoes": {"view": True}},
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def outsider(db: AsyncSession) -> User:
    """Usuário sem qualquer permissão em integracoes."""
    email = f"out-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(
        open_id=f"email:{email}",
        email=email,
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest.mark.asyncio
async def test_crud_lifecycle(
    client: AsyncClient,
    admin: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(admin)

    # Cria.
    r = await client.post(
        "/api/automacoes",
        json={
            "nome": "Sincronização de pedidos Bling",
            "descricao": "Re-ingere pedidos alterados nas últimas 2h.",
            "frequencia": "a cada 1h",
            "categoria": "Sync",
        },
    )
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    assert r.json()["nome"] == "Sincronização de pedidos Bling"
    assert r.json()["ativa"] is True

    # Lista.
    r = await client.get("/api/automacoes")
    assert any(a["id"] == aid for a in r.json())

    # Edita (marca como parada).
    r = await client.patch(f"/api/automacoes/{aid}", json={"ativa": False, "frequencia": "a cada 30min"})
    assert r.status_code == 200
    assert r.json()["ativa"] is False
    assert r.json()["frequencia"] == "a cada 30min"

    # Remove.
    r = await client.delete(f"/api/automacoes/{aid}")
    assert r.status_code == 204
    r = await client.get("/api/automacoes")
    assert not any(a["id"] == aid for a in r.json())


@pytest.mark.asyncio
async def test_viewer_can_list_not_edit(
    client: AsyncClient,
    admin: User,
    viewer: User,
    auth_as: Callable[[User | None], None],
):
    # Admin cria uma.
    auth_as(admin)
    r = await client.post("/api/automacoes", json={"nome": "Refresh de tokens"})
    assert r.status_code == 201

    # Viewer (view-only) consegue LISTAR mas não CRIAR.
    auth_as(viewer)
    r = await client.get("/api/automacoes")
    assert r.status_code == 200
    assert any(a["nome"] == "Refresh de tokens" for a in r.json())

    r = await client.post("/api/automacoes", json={"nome": "hack"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_outsider_forbidden(
    client: AsyncClient,
    outsider: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(outsider)
    r = await client.get("/api/automacoes")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_patch_not_found(
    client: AsyncClient,
    admin: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(admin)
    r = await client.patch(f"/api/automacoes/{uuid.uuid4()}", json={"nome": "z"})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "automacao_not_found"
