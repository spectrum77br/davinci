"""POST /importacao/products cria automaticamente a linha (base) da aba Kit.

Regra (só Celular): ao criar um produto de Celular ele deve aparecer como
linha na matriz do Kit (import_kit_bases), pelo SKU completo — espelhando o
seed 0117. Fora de escopo, sem base:
  * Mala/Eletro (mala usa 1 base por modelo, tamanho é coluna; eletro sem Kit)
  * Acessórios celular (sem ' - <Cor>' no modelo — são componentes de kit)

Idempotente: SKU que já é base não duplica (sku_base é UNIQUE global).
"""
from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ImportKitBase, User, UserRole, UserStatus

PERM_EDIT = {"importacao": {"view": True, "edit": True, "delete": True}}


@pytest_asyncio.fixture
async def user_edit(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:ka-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"ka-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions=PERM_EDIT,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _bases_for(db: AsyncSession, sku: str) -> list[ImportKitBase]:
    return list((await db.execute(
        select(ImportKitBase).where(ImportKitBase.sku_base == sku)
    )).scalars().all())


@pytest.mark.asyncio
async def test_create_celular_device_creates_kit_base(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], user_edit: User,
):
    """Aparelho celular (modelo com ' - <Cor>') → 1 base, cor derivada."""
    auth_as(user_edit)
    sku = f"itst{uuid.uuid4().hex[:5]}.sp"
    r = await client.post("/api/importacao/products", json={
        "categoria": "celular",
        "sku": sku,
        "modelo_bling": "Apple Ipad Teste 128 GB - Azul",
    })
    assert r.status_code == 201, r.text

    bases = await _bases_for(db, sku)
    assert len(bases) == 1
    b = bases[0]
    assert b.categoria == "celular"
    assert b.modelo_bling == "Apple Ipad Teste 128 GB - Azul"
    assert b.cor == "Azul"  # trecho após o último ' - ', 1ª maiúscula
    assert b.ordem is not None

    # Aparece no grid do Kit
    g = await client.get("/api/importacao/kit?categoria=celular")
    assert g.status_code == 200, g.text
    assert any(x["sku_base"] == sku for x in g.json()["bases"])


@pytest.mark.asyncio
async def test_create_celular_accessory_no_base(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], user_edit: User,
):
    """Acessório celular (sem ' - <Cor>') não vira base — é componente."""
    auth_as(user_edit)
    sku = f"atst{uuid.uuid4().hex[:5]}.cd"
    r = await client.post("/api/importacao/products", json={
        "categoria": "celular",
        "sku": sku,
        "modelo_bling": "Uranyx Fone com fio TST",
    })
    assert r.status_code == 201, r.text
    assert await _bases_for(db, sku) == []


@pytest.mark.asyncio
async def test_create_mala_no_base(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], user_edit: User,
):
    """Mala fica fora de escopo mesmo com ' - <Cor>' no modelo (o tamanho
    é coluna/variação, não linha) — nenhuma base é criada."""
    auth_as(user_edit)
    sku = f"ztst{uuid.uuid4().hex[:5]}.8"
    r = await client.post("/api/importacao/products", json={
        "categoria": "mala",
        "sku": sku,
        "modelo_bling": "Mala Teste tamanho 8 - Branca",
        "cor": "branca",
    })
    assert r.status_code == 201, r.text
    assert await _bases_for(db, sku) == []


@pytest.mark.asyncio
async def test_create_celular_duplicate_sku_no_dup_base(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], user_edit: User,
):
    """SKU que já é base (seed/produto recriado) não duplica nem quebra na
    UNIQUE(sku_base)."""
    auth_as(user_edit)
    sku = f"itst{uuid.uuid4().hex[:5]}.sa"
    db.add(ImportKitBase(
        categoria="celular", modelo_bling="Apple Ipad Teste 128 GB - Cinza",
        sku_base=sku, cor="Cinza", ordem=999,
    ))
    await db.commit()

    r = await client.post("/api/importacao/products", json={
        "categoria": "celular",
        "sku": sku,
        "modelo_bling": "Apple Ipad Teste 128 GB - Cinza",
    })
    assert r.status_code == 201, r.text
    assert len(await _bases_for(db, sku)) == 1  # sem duplicata
