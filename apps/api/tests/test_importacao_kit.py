"""Endpoints da aba Kit em /importacao.

Cobertura:
  * GET /api/importacao/kit devolve {variations, bases, marks}
  * PUT /api/importacao/kit/mark com marked=true cria (idempotente)
  * PUT /api/importacao/kit/mark com marked=false deleta (idempotente)
  * PUT exige permissão importacao:edit
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
    ImportKitBase,
    ImportKitMark,
    ImportKitVariation,
    User,
    UserRole,
    UserStatus,
)

PERM_VIEW = {"importacao": {"view": True, "edit": False, "delete": False}}
PERM_EDIT = {"importacao": {"view": True, "edit": True, "delete": False}}


@pytest_asyncio.fixture
async def user_kit_edit(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:ke-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"ke-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions=PERM_EDIT,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def user_kit_view(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:kv-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"kv-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions=PERM_VIEW,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def seeded_kit(db: AsyncSession) -> dict:
    """Insere 2 variations + 2 bases + 1 mark pra os testes.
    Retorna ids pra usar nos asserts."""
    v1 = ImportKitVariation(code="8", label="8", ordem=1, highlight=True)
    v2 = ImportKitVariation(code="12+18", label="12+18", ordem=2, highlight=False)
    b1 = ImportKitBase(modelo_bling="M2 lisa", sku_base="b001", cor="branca", ordem=1)
    b2 = ImportKitBase(modelo_bling="M1 listrada", sku_base="b005", cor="preto", ordem=2)
    db.add_all([v1, v2, b1, b2])
    await db.commit()
    await db.refresh(v1)
    await db.refresh(v2)
    await db.refresh(b1)
    await db.refresh(b2)
    m = ImportKitMark(base_id=b1.id, variation_id=v1.id)
    db.add(m)
    await db.commit()
    return {
        "v1": str(v1.id), "v2": str(v2.id),
        "b1": str(b1.id), "b2": str(b2.id),
    }


@pytest.mark.asyncio
async def test_get_kit_returns_variations_bases_and_marks(
    client: AsyncClient,
    user_kit_view: User,
    auth_as: Callable[[User | None], None],
    seeded_kit: dict,
):
    auth_as(user_kit_view)
    r = await client.get("/api/importacao/kit")
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["variations"]) == 2
    assert len(data["bases"]) == 2
    assert len(data["marks"]) == 1
    # Variations: ordenadas por `ordem`.
    assert data["variations"][0]["ordem"] == 1
    # Bases: ordenadas alfabeticamente por modelo_bling (LOWER).
    # "M1 listrada" (b005) vem antes de "M2 lisa" (b001).
    assert data["bases"][0]["sku_base"] == "b005"
    assert data["bases"][1]["sku_base"] == "b001"
    # Mark refere o cruzamento certo
    assert data["marks"][0]["base_id"] == seeded_kit["b1"]
    assert data["marks"][0]["variation_id"] == seeded_kit["v1"]


@pytest.mark.asyncio
async def test_put_mark_true_creates_idempotent(
    db: AsyncSession,
    client: AsyncClient,
    user_kit_edit: User,
    auth_as: Callable[[User | None], None],
    seeded_kit: dict,
):
    auth_as(user_kit_edit)
    payload = {"base_id": seeded_kit["b2"], "variation_id": seeded_kit["v2"], "marked": True}
    r = await client.put("/api/importacao/kit/mark", json=payload)
    assert r.status_code == 204, r.text
    # Mark criada
    rows = (await db.execute(select(ImportKitMark))).scalars().all()
    assert len(rows) == 2  # 1 seeded + 1 novo
    # Idempotente: 2ª chamada não duplica
    r2 = await client.put("/api/importacao/kit/mark", json=payload)
    assert r2.status_code == 204
    rows2 = (await db.execute(select(ImportKitMark))).scalars().all()
    assert len(rows2) == 2


@pytest.mark.asyncio
async def test_put_mark_false_deletes_idempotent(
    db: AsyncSession,
    client: AsyncClient,
    user_kit_edit: User,
    auth_as: Callable[[User | None], None],
    seeded_kit: dict,
):
    auth_as(user_kit_edit)
    payload = {"base_id": seeded_kit["b1"], "variation_id": seeded_kit["v1"], "marked": False}
    r = await client.put("/api/importacao/kit/mark", json=payload)
    assert r.status_code == 204
    # Mark deletada
    rows = (await db.execute(select(ImportKitMark))).scalars().all()
    assert len(rows) == 0
    # Idempotente: deletar de novo é no-op
    r2 = await client.put("/api/importacao/kit/mark", json=payload)
    assert r2.status_code == 204
    rows2 = (await db.execute(select(ImportKitMark))).scalars().all()
    assert len(rows2) == 0


@pytest.mark.asyncio
async def test_put_mark_requires_edit_permission(
    client: AsyncClient,
    user_kit_view: User,
    auth_as: Callable[[User | None], None],
    seeded_kit: dict,
):
    auth_as(user_kit_view)
    payload = {"base_id": seeded_kit["b2"], "variation_id": seeded_kit["v2"], "marked": True}
    r = await client.put("/api/importacao/kit/mark", json=payload)
    assert r.status_code == 403, r.text
