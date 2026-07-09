"""Faturas CRUD (admin-only) + contador de vencimento da sidebar.

A feature: aba Faturas no Admin guarda assinaturas/planos recorrentes (ex.:
plano de 12 meses do Higgsfield). Só admin gerencia. `vencendo-count` alimenta
a bolinha vermelha na sidebar (data_vencimento <= hoje+1).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date, timedelta

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
async def normal(db: AsyncSession) -> User:
    email = f"usr-{uuid.uuid4().hex[:6]}@davinci-test.com"
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
    venc = (date.today() + timedelta(days=30)).isoformat()

    # Cria.
    r = await client.post(
        "/api/faturas",
        json={
            "servico": "Higgsfield",
            "plano": "Plano 12 meses",
            "valor": 300.5,
            "data_vencimento": venc,
        },
    )
    assert r.status_code == 201, r.text
    fid = r.json()["id"]
    assert r.json()["servico"] == "Higgsfield"
    assert r.json()["data_vencimento"] == venc

    # Lista.
    r = await client.get("/api/faturas")
    assert any(f["id"] == fid for f in r.json())

    # Edita (renova pro próximo ciclo).
    novo = (date.today() + timedelta(days=395)).isoformat()
    r = await client.patch(f"/api/faturas/{fid}", json={"data_vencimento": novo})
    assert r.status_code == 200
    assert r.json()["data_vencimento"] == novo

    # Remove.
    r = await client.delete(f"/api/faturas/{fid}")
    assert r.status_code == 204
    r = await client.get("/api/faturas")
    assert not any(f["id"] == fid for f in r.json())


@pytest.mark.asyncio
async def test_vencendo_count(
    client: AsyncClient,
    admin: User,
    auth_as: Callable[[User | None], None],
):
    """Conta só as que vencem até amanhã (hoje+1). Uma daqui a 10 dias fica
    de fora."""
    auth_as(admin)
    for dias in (-2, 0, 1, 10):  # vencida, hoje, amanhã (contam), 10d (não)
        r = await client.post(
            "/api/faturas",
            json={
                "servico": f"svc-{dias}",
                "data_vencimento": (date.today() + timedelta(days=dias)).isoformat(),
            },
        )
        assert r.status_code == 201, r.text

    r = await client.get("/api/faturas/vencendo-count")
    assert r.status_code == 200
    assert r.json()["count"] == 3


@pytest.mark.asyncio
async def test_non_admin_forbidden(
    client: AsyncClient,
    normal: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(normal)
    r = await client.get("/api/faturas")
    assert r.status_code == 403
    r = await client.post(
        "/api/faturas",
        json={"servico": "x", "data_vencimento": date.today().isoformat()},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_patch_not_found(
    client: AsyncClient,
    admin: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(admin)
    r = await client.patch(
        f"/api/faturas/{uuid.uuid4()}", json={"servico": "z"}
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "fatura_not_found"
