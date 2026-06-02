"""POST /importacao/kit/variations — botão "Criar Kit" pra Mala e Celular.

Cada categoria tem regras de validação diferentes pra `code`:
  - mala: precisa de ao menos 1 tamanho numérico (8, 12+20, etc.)
  - celular: padrão estrito aXXX(+aYYY)*

Duplicata em (categoria, code) → 409 (mantém compat com partial unique
do celular + bloqueia via UI a criação de mais duplicatas em mala).
"""
from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ImportKitVariation,
    User,
    UserRole,
    UserStatus,
)

PERM = {"importacao": {"view": True, "edit": True, "delete": True}}


@pytest_asyncio.fixture
async def admin(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:kv-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"kv-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
        permissions=PERM,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest.mark.asyncio
async def test_post_celular_a007_carregador(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin: User,
):
    """Celular code 'a007' válido → 201, ordem = max + 1."""
    auth_as(admin)
    # Seed: 1 variation já existente em celular (ordem=1)
    db.add(ImportKitVariation(
        id=uuid.uuid4(), categoria="celular",
        code="a001", label="Fone fio", ordem=1, highlight=False,
    ))
    await db.commit()

    r = await client.post("/api/importacao/kit/variations", json={
        "categoria": "celular", "code": "a007", "label": "Carregador",
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["code"] == "a007"
    assert body["label"] == "Carregador"
    assert body["ordem"] == 2  # próxima após o a001


@pytest.mark.asyncio
async def test_post_celular_codigo_estilo_mala_rejeita(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin: User,
):
    """Celular não aceita '8+18' (formato mala)."""
    auth_as(admin)
    r = await client.post("/api/importacao/kit/variations", json={
        "categoria": "celular", "code": "8+18", "label": "X",
    })
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_variation_code"


@pytest.mark.asyncio
async def test_post_mala_tamanho_numerico(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin: User,
):
    """Mala code '8+18' válido → 201."""
    auth_as(admin)
    r = await client.post("/api/importacao/kit/variations", json={
        "categoria": "mala", "code": "8+18", "label": "M1 mala 8+18",
    })
    assert r.status_code == 201, r.text
    assert r.json()["code"] == "8+18"
    assert r.json()["ordem"] == 1  # primeira variation em mala neste teste


@pytest.mark.asyncio
async def test_post_mala_so_acessorio_rejeita(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin: User,
):
    """Mala precisa de pelo menos 1 tamanho numérico; só 'a007' rejeita."""
    auth_as(admin)
    r = await client.post("/api/importacao/kit/variations", json={
        "categoria": "mala", "code": "a007", "label": "X",
    })
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_variation_code"


@pytest.mark.asyncio
async def test_post_celular_code_duplicado_409(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin: User,
):
    """Code já existe na categoria → 409 com mensagem específica."""
    auth_as(admin)
    db.add(ImportKitVariation(
        id=uuid.uuid4(), categoria="celular",
        code="a001", label="Fone fio", ordem=1, highlight=False,
    ))
    await db.commit()

    r = await client.post("/api/importacao/kit/variations", json={
        "categoria": "celular", "code": "a001", "label": "Outra",
    })
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "variation_code_exists"
    assert "celular" in r.json()["detail"]["message"].lower()
