"""Selector de categoria (mala/eletro/celular) em /importacao.

Cobertura:
  * GET /products?categoria=eletro devolve só registros eletro
  * POST /products com categoria=eletro cria row com a categoria certa
  * GET /kit?categoria=eletro → 404 (eletro não tem kit)
  * GET /kit?categoria=mala → 200 (grid normal)
  * naming: generate_product_name dispatch por categoria
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ImportProduct, User, UserRole, UserStatus
from app.services.importacao_naming import (
    generate_celular_name,
    generate_mala_name,
    generate_product_name,
)

PERM_EDIT = {"importacao": {"view": True, "edit": True, "delete": False}}


@pytest_asyncio.fixture
async def user_imp_edit(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:ie-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"ie-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions=PERM_EDIT,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def seeded_products(db: AsyncSession) -> None:
    """1 mala + 1 eletro pra checar o filtro por categoria."""
    db.add_all([
        ImportProduct(sku="b001.8", categoria="mala", modelo_bling="M2 lisa",
                      cor="branca", custo_bling=Decimal("49.00")),
        ImportProduct(sku="uaf001m1.110", categoria="eletro",
                      modelo_bling="Airfryer vidro", custo_bling=Decimal("120.00")),
    ])
    await db.commit()


@pytest.mark.asyncio
async def test_products_filter_by_categoria(
    client: AsyncClient,
    user_imp_edit: User,
    auth_as: Callable[[User | None], None],
    seeded_products: None,
):
    auth_as(user_imp_edit)
    r = await client.get("/api/importacao/products?categoria=eletro")
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["sku"] == "uaf001m1.110"
    assert rows[0]["categoria"] == "eletro"
    # nome_gerado eletro = só o modelo (sem "Mala"/tamanho/cor)
    assert rows[0]["nome_gerado"] == "Airfryer vidro"

    r_mala = await client.get("/api/importacao/products?categoria=mala")
    assert r_mala.status_code == 200
    mala_rows = r_mala.json()
    assert {x["sku"] for x in mala_rows} == {"b001.8"}


@pytest.mark.asyncio
async def test_create_product_with_categoria(
    db: AsyncSession,
    client: AsyncClient,
    user_imp_edit: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(user_imp_edit)
    payload = {
        "categoria": "eletro",
        "modelo_bling": "Cafeteira Expresso",
        "sku": "ucm001m1.110",
        "custo_bling": "89.00",
    }
    r = await client.post("/api/importacao/products", json=payload)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["categoria"] == "eletro"
    assert body["nome_gerado"] == "Cafeteira Expresso"

    row = (await db.execute(
        select(ImportProduct).where(ImportProduct.sku == "ucm001m1.110")
    )).scalar_one()
    assert row.categoria == "eletro"


@pytest.mark.asyncio
async def test_kit_404_for_eletro(
    client: AsyncClient,
    user_imp_edit: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(user_imp_edit)
    r = await client.get("/api/importacao/kit?categoria=eletro")
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_kit_200_for_mala(
    client: AsyncClient,
    user_imp_edit: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(user_imp_edit)
    r = await client.get("/api/importacao/kit?categoria=mala")
    assert r.status_code == 200, r.text
    data = r.json()
    assert set(data.keys()) == {"variations", "bases", "marks"}


# ── naming dispatch (puro, sem DB) ──────────────────────────────────


def test_generate_product_name_eletro():
    assert generate_product_name("eletro", "Smart TV LG 55", None, None) == "Smart TV LG 55"


def test_generate_product_name_celular_usa_modelo_bling_direto():
    """Spec G1: 'nome seguir → modelo bling'. Sem prefixo 'Mala', sem
    tamanho extraído do SKU — cor já vem dentro do modelo."""
    got = generate_product_name(
        "celular", "Apple Watch SE 3 GPS 40mm - Preto", "i230.sa", "Preto",
    )
    assert got == "Apple Watch SE 3 GPS 40mm - Preto"


def test_generate_celular_name_fallback_quando_modelo_none():
    assert generate_celular_name(None, "i230.sa", "Preto") == "Produto celular"


def test_generate_celular_name_trima_modelo():
    got = generate_celular_name("  Hotwav A17 Pro Max  ", "h017.sa", None)
    assert got == "Hotwav A17 Pro Max"


def test_generate_product_name_mala():
    assert generate_product_name("mala", "M2 lisa", "b001.28", "Roxo") == (
        generate_mala_name("M2 lisa", "b001.28", "Roxo")
    )


def test_generate_product_name_unknown_raises():
    with pytest.raises(ValueError):
        generate_product_name("xpto", "x", None, None)
