"""custo_realizado é computed: média ponderada Σ(qty × custoBRL_lote) / Σ(qty).

Migration 0123 dropou a coluna; GET /products calcula em tempo real.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ImportLote,
    ImportLoteItem,
    ImportProduct,
    User,
    UserRole,
    UserStatus,
)

PERM = {"importacao": {"view": True, "edit": True, "delete": True}}


@pytest_asyncio.fixture
async def admin_view(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:cr-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"cr-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
        permissions=PERM,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _make_lote(
    db: AsyncSession, *, nome: str, taxa: Decimal, frete: Decimal,
    adicional: Decimal,
) -> ImportLote:
    lt = ImportLote(
        id=uuid.uuid4(), categoria="celular", nome=nome,
        abertura=date(2026, 6, 1),
        taxa=taxa, frete_pct=frete, adicional=adicional,
    )
    db.add(lt)
    await db.flush()
    return lt


@pytest.mark.asyncio
async def test_custo_realizado_media_ponderada(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin_view: User,
):
    """2 lotes: qty=10 custoBRL=1000 e qty=20 custoBRL=2000.
    Esperado: (10×1000 + 20×2000) / 30 = 50000/30 = 1666.67."""
    auth_as(admin_view)
    prod = ImportProduct(
        id=uuid.uuid4(), categoria="celular", sku="i999.sa",
        modelo_bling="Apple Test", custo_bling=Decimal("0"),
    )
    db.add(prod)
    await db.flush()

    # Lote 1: taxa=5, frete=0, adicional=0, valor_usd=200 → custoBRL=1000
    l1 = await _make_lote(
        db, nome="L1", taxa=Decimal("5"),
        frete=Decimal("0"), adicional=Decimal("0"),
    )
    db.add(ImportLoteItem(
        id=uuid.uuid4(), lote_id=l1.id, product_id=prod.id,
        quantidade=10, valor_usd=Decimal("200"),
    ))
    # Lote 2: taxa=5, frete=0, adicional=0, valor_usd=400 → custoBRL=2000
    l2 = await _make_lote(
        db, nome="L2", taxa=Decimal("5"),
        frete=Decimal("0"), adicional=Decimal("0"),
    )
    db.add(ImportLoteItem(
        id=uuid.uuid4(), lote_id=l2.id, product_id=prod.id,
        quantidade=20, valor_usd=Decimal("400"),
    ))
    await db.commit()

    r = await client.get("/api/importacao/products?categoria=celular")
    assert r.status_code == 200, r.text
    by_sku = {p["sku"]: p for p in r.json()}
    assert "i999.sa" in by_sku
    assert Decimal(by_sku["i999.sa"]["custo_realizado"]) == Decimal("1666.67")


@pytest.mark.asyncio
async def test_custo_realizado_sem_lote_eh_none(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin_view: User,
):
    """Produto sem nenhum lote item → custo_realizado = null."""
    auth_as(admin_view)
    prod = ImportProduct(
        id=uuid.uuid4(), categoria="celular", sku="i998.sa",
        modelo_bling="Test Sem Lote", custo_bling=Decimal("0"),
    )
    db.add(prod)
    await db.commit()

    r = await client.get("/api/importacao/products?categoria=celular")
    by_sku = {p["sku"]: p for p in r.json()}
    assert by_sku["i998.sa"]["custo_realizado"] is None


@pytest.mark.asyncio
async def test_custo_realizado_ignora_item_sem_valor_usd(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin_view: User,
):
    """Lote com qty mas SEM valor_usd → linha ignorada no cálculo
    (não dá pra calcular custoBRL sem valor). Outras linhas com valor
    contam normal."""
    auth_as(admin_view)
    prod = ImportProduct(
        id=uuid.uuid4(), categoria="celular", sku="i997.sa",
        modelo_bling="Test Misto", custo_bling=Decimal("0"),
    )
    db.add(prod)
    await db.flush()

    # Lote A: COM valor_usd → conta (10×1000 = 10000)
    la = await _make_lote(
        db, nome="LA", taxa=Decimal("5"),
        frete=Decimal("0"), adicional=Decimal("0"),
    )
    db.add(ImportLoteItem(
        id=uuid.uuid4(), lote_id=la.id, product_id=prod.id,
        quantidade=10, valor_usd=Decimal("200"),
    ))
    # Lote B: SEM valor_usd → ignorado
    lb = await _make_lote(
        db, nome="LB", taxa=Decimal("5"),
        frete=Decimal("0"), adicional=Decimal("0"),
    )
    db.add(ImportLoteItem(
        id=uuid.uuid4(), lote_id=lb.id, product_id=prod.id,
        quantidade=99, valor_usd=None,
    ))
    await db.commit()

    r = await client.get("/api/importacao/products?categoria=celular")
    by_sku = {p["sku"]: p for p in r.json()}
    # Só Lote A entra: 10000 / 10 = 1000.00
    assert Decimal(by_sku["i997.sa"]["custo_realizado"]) == Decimal("1000.00")
