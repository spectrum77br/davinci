"""Arquivar/ativar loja suspensa (feature Lojas → archived_at).

Cobre o fluxo: arquivar uma StoreInfo tira ela de /store-info (default),
propaga o archived_at pra integração vinculada, some da Tabela de Preço
(/accounts) e de /integrations; /store-info?archived=true lista as arquivadas;
/unarchive reverte loja + integração.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Integration,
    IntegrationPlatform,
    PricingAccount,
    PricingPlatform,
    Segment,
    StoreInfo,
    User,
    UserRole,
    UserStatus,
)

PERM = {
    "tabela_precos": {"view": True, "edit": True, "delete": True},
    "tabela_precos_contas": {"view": True, "edit": True, "delete": True},
    "produtos": {"view": True, "edit": True, "delete": True},
}


@pytest_asyncio.fixture
async def user_arch(db: AsyncSession) -> User:
    email = f"arch-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(
        open_id=f"email:{email}",
        email=email,
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions=PERM,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def archive_setup(db: AsyncSession, user_arch: User) -> dict[str, object]:
    seg = Segment(name="Celular", slug="celular", sort_order=1)
    db.add(seg)
    await db.flush()

    integ = Integration(
        user_id=user_arch.id,
        platform=IntegrationPlatform.SHOPEE,
        name="loja-shopee",
        credentials=b"x",
    )
    db.add(integ)
    await db.flush()

    info = StoreInfo(
        user_id=user_arch.id,
        platform="shopee",
        account_name="loja-shopee",
        integration_id=integ.id,
    )
    db.add(info)
    await db.flush()

    acc = PricingAccount(
        user_id=user_arch.id,
        name="loja-shopee",
        platform=PricingPlatform.SHOPEE,
        segment_id=seg.id,
        store_info_id=info.id,
        integration_id=integ.id,
    )
    db.add(acc)
    await db.commit()
    await db.refresh(info)
    await db.refresh(integ)
    await db.refresh(acc)
    return {"info_id": info.id, "integ_id": integ.id, "acc_id": acc.id}


@pytest.mark.asyncio
async def test_archive_hides_store_and_cascades(
    db: AsyncSession,
    client: AsyncClient,
    user_arch: User,
    auth_as: Callable[[User | None], None],
    archive_setup: dict[str, object],
):
    auth_as(user_arch)
    info_id = archive_setup["info_id"]
    integ_id = archive_setup["integ_id"]

    # Antes: aparece em Lojas, Tabela de Preço e Integrações.
    r = await client.get("/api/pricing/store-info")
    assert any(s["id"] == str(info_id) for s in r.json())
    r = await client.get("/api/pricing/accounts")
    assert any(a["id"] == str(archive_setup["acc_id"]) for a in r.json())
    r = await client.get("/api/integrations")
    assert any(i["id"] == str(integ_id) for i in r.json())

    # Arquiva.
    r = await client.post(f"/api/pricing/store-info/{info_id}/archive")
    assert r.status_code == 200
    assert r.json()["archived_at"] is not None

    # A integração vinculada foi arquivada junto.
    integ = (
        await db.execute(select(Integration).where(Integration.id == integ_id))
    ).scalar_one()
    await db.refresh(integ)
    assert integ.archived_at is not None

    # Some de Lojas (default), Tabela de Preço e Integrações.
    r = await client.get("/api/pricing/store-info")
    assert not any(s["id"] == str(info_id) for s in r.json())
    r = await client.get("/api/pricing/accounts")
    assert not any(a["id"] == str(archive_setup["acc_id"]) for a in r.json())
    r = await client.get("/api/integrations")
    assert not any(i["id"] == str(integ_id) for i in r.json())

    # Aparece na aba Arquivadas.
    r = await client.get("/api/pricing/store-info?archived=true")
    assert any(s["id"] == str(info_id) for s in r.json())


@pytest.mark.asyncio
async def test_unarchive_reverts_store_and_integration(
    db: AsyncSession,
    client: AsyncClient,
    user_arch: User,
    auth_as: Callable[[User | None], None],
    archive_setup: dict[str, object],
):
    auth_as(user_arch)
    info_id = archive_setup["info_id"]
    integ_id = archive_setup["integ_id"]

    r = await client.post(f"/api/pricing/store-info/{info_id}/archive")
    assert r.status_code == 200

    r = await client.post(f"/api/pricing/store-info/{info_id}/unarchive")
    assert r.status_code == 200
    assert r.json()["archived_at"] is None

    integ = (
        await db.execute(select(Integration).where(Integration.id == integ_id))
    ).scalar_one()
    await db.refresh(integ)
    assert integ.archived_at is None

    # Volta pra Lojas ativas e some das arquivadas.
    r = await client.get("/api/pricing/store-info")
    assert any(s["id"] == str(info_id) for s in r.json())
    r = await client.get("/api/pricing/store-info?archived=true")
    assert not any(s["id"] == str(info_id) for s in r.json())
    r = await client.get("/api/integrations")
    assert any(i["id"] == str(integ_id) for i in r.json())


@pytest.mark.asyncio
async def test_archive_hides_account_from_grid(
    db: AsyncSession,
    client: AsyncClient,
    user_arch: User,
    auth_as: Callable[[User | None], None],
    archive_setup: dict[str, object],
):
    """Regressão: a conta arquivada saía de /accounts (a lista) mas continuava
    como COLUNA no /grid (a matriz de preços), porque o grid não aplicava o
    filtro de arquivadas. Agora os dois usam a mesma exclusão."""
    auth_as(user_arch)
    info_id = archive_setup["info_id"]
    acc_id = str(archive_setup["acc_id"])

    # Antes: a conta é coluna no grid.
    r = await client.get("/api/pricing/grid")
    assert r.status_code == 200, r.text
    assert any(a["id"] == acc_id for a in r.json()["accounts"])

    # Arquiva a loja.
    r = await client.post(f"/api/pricing/store-info/{info_id}/archive")
    assert r.status_code == 200

    # Depois: some do grid também.
    r = await client.get("/api/pricing/grid")
    assert r.status_code == 200, r.text
    assert not any(a["id"] == acc_id for a in r.json()["accounts"])


@pytest.mark.asyncio
async def test_archive_not_found(
    client: AsyncClient,
    user_arch: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(user_arch)
    r = await client.post(f"/api/pricing/store-info/{uuid.uuid4()}/archive")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "store_info_not_found"
