"""Worker que cria produto simples no Bling pra rows de import_products.

Mocked BlingClient — não bate na API real. Cobertura:
  * happy path: pending → sent, cria Product local
  * Bling rejeita (raise): status='error', mensagem salva, Product NÃO criado
  * idempotência: re-rodar com bling_product_id setado é no-op
  * sem integration: status='error'
  * Product local existente: linka bling_product_id sem duplicar
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ImportProduct,
    Integration,
    IntegrationPlatform,
    Product,
    User,
    UserRole,
    UserStatus,
)
from app.security.cipher import encrypt_json
from app.services.import_product_bling_create import sync_import_product_to_bling


@pytest_asyncio.fixture
async def bling_integration(db: AsyncSession) -> Integration:
    owner = User(
        open_id=f"email:bi-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"bi-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.ADMIN, status=UserStatus.ACTIVE, permissions={},
    )
    db.add(owner)
    await db.commit()
    await db.refresh(owner)
    integ = Integration(
        user_id=owner.id,
        platform=IntegrationPlatform.BLING,
        name="Bling Test",
        credentials=encrypt_json({
            "access_token": "tok", "refresh_token": "ref",
            "client_id": "cid", "client_secret": "csec",
            "expires_at": int(datetime.now(UTC).timestamp()) + 3600,
        }),
    )
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    return integ


@pytest_asyncio.fixture
async def import_row(db: AsyncSession) -> ImportProduct:
    row = ImportProduct(
        sku="b042.28",
        modelo_bling="Lisa M2",
        cor="Roxo Escuro",
        custo_bling=Decimal("49.00"),
        bling_sync_status="pending",
        bling_sync_marked_at=datetime.now(UTC),
        bling_sync_attempted_at=datetime.now(UTC),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


def _install_bling_mock(monkeypatch, *, return_id: int = 99999, raise_exc: Exception | None = None):
    async def fake_find_or_create_category(self, name):
        return 777

    async def fake_create_product(self, **kwargs):
        if raise_exc is not None:
            raise raise_exc
        return {"id": return_id, "echo": kwargs}

    from app.services.marketplaces.bling import BlingClient
    monkeypatch.setattr(BlingClient, "find_or_create_category", fake_find_or_create_category)
    monkeypatch.setattr(BlingClient, "create_product", fake_create_product)


@pytest.mark.asyncio
async def test_happy_path_creates_product_and_links(
    db: AsyncSession, bling_integration: Integration, import_row: ImportProduct, monkeypatch,
):
    _install_bling_mock(monkeypatch, return_id=88888)
    result = await sync_import_product_to_bling(import_row.id)
    assert result["ok"] is True
    assert result["bling_product_id"] == 88888

    await db.refresh(import_row)
    assert import_row.bling_product_id == 88888
    assert import_row.bling_sync_status == "sent"
    assert import_row.bling_sync_done_at is not None
    assert import_row.bling_sync_error is None

    # Product local foi criado
    p = (await db.execute(
        select(Product).where(Product.sku == "b042.28")
    )).scalar_one()
    assert p.bling_product_id == 88888
    assert p.name == "Mala Lisa M2 tamanho 28 - Roxo Escuro"
    assert p.bling_cost_price == Decimal("49.0000")
    assert p.situacao == "A"
    assert p.formato == "S"
    assert p.user_id == bling_integration.user_id


@pytest.mark.asyncio
async def test_bling_failure_marks_error(
    db: AsyncSession, bling_integration: Integration, import_row: ImportProduct, monkeypatch,
):
    _install_bling_mock(monkeypatch, raise_exc=RuntimeError("oops bling"))
    result = await sync_import_product_to_bling(import_row.id)
    assert result["ok"] is False
    assert "bling_call_failed" in result["error"]

    await db.refresh(import_row)
    assert import_row.bling_product_id is None
    assert import_row.bling_sync_status == "error"
    assert "oops bling" in (import_row.bling_sync_error or "")

    # Product local NÃO foi criado
    rows = (await db.execute(
        select(Product).where(Product.sku == "b042.28")
    )).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_idempotent_when_already_synced(
    db: AsyncSession, bling_integration: Integration, import_row: ImportProduct, monkeypatch,
):
    """Rodar 2x não duplica chamadas — segunda é no-op."""
    import_row.bling_product_id = 77777
    import_row.bling_sync_status = "sent"
    await db.commit()

    call_count = {"create_product": 0}

    from app.services.marketplaces.bling import BlingClient

    async def fake_create(self, **kwargs):
        call_count["create_product"] += 1
        return {"id": 99999}

    async def fake_cat(self, name):
        return 777

    monkeypatch.setattr(BlingClient, "create_product", fake_create)
    monkeypatch.setattr(BlingClient, "find_or_create_category", fake_cat)

    result = await sync_import_product_to_bling(import_row.id)
    assert result["ok"] is True
    assert result.get("skipped") is True
    assert result["bling_product_id"] == 77777
    assert call_count["create_product"] == 0


@pytest.mark.asyncio
async def test_no_bling_integration_errors(
    db: AsyncSession, import_row: ImportProduct, monkeypatch,
):
    """Sem Integration de Bling → status='error'."""
    _install_bling_mock(monkeypatch)
    result = await sync_import_product_to_bling(import_row.id)
    assert result["ok"] is False
    assert result["error"] == "no_bling_integration"
    await db.refresh(import_row)
    assert import_row.bling_sync_status == "error"


@pytest.mark.asyncio
async def test_existing_local_product_gets_bling_id_link(
    db: AsyncSession, bling_integration: Integration, import_row: ImportProduct, monkeypatch,
):
    """Product local já existia (SKU igual) sem bling_product_id —
    o worker linka sem duplicar."""
    pre_existing = Product(
        user_id=bling_integration.user_id,
        sku="b042.28",
        name="Mala antiga",
        stock=10,
        bling_product_id=None,
    )
    db.add(pre_existing)
    await db.commit()
    pre_id = pre_existing.id

    _install_bling_mock(monkeypatch, return_id=55555)
    result = await sync_import_product_to_bling(import_row.id)
    assert result["ok"] is True

    # Worker rodou em outra sessão — expira cache do ORM pra ler do DB.
    db.expire_all()
    rows = (await db.execute(
        select(Product).where(Product.sku == "b042.28")
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].id == pre_id
    assert rows[0].bling_product_id == 55555


@pytest.mark.asyncio
async def test_passes_correct_payload_to_bling(
    db: AsyncSession, bling_integration: Integration, import_row: ImportProduct, monkeypatch,
):
    """Verifica nome canônico + categoria + formato Simples."""
    captured: dict[str, Any] = {}

    from app.services.marketplaces.bling import BlingClient

    async def fake_create(self, **kwargs):
        captured.update(kwargs)
        return {"id": 88888}

    async def fake_cat(self, name):
        captured["_category_name"] = name
        return 777

    monkeypatch.setattr(BlingClient, "create_product", fake_create)
    monkeypatch.setattr(BlingClient, "find_or_create_category", fake_cat)

    await sync_import_product_to_bling(import_row.id)

    assert captured["sku"] == "b042.28"
    assert captured["name"] == "Mala Lisa M2 tamanho 28 - Roxo Escuro"
    assert captured["formato"] == "S"
    assert captured["category_id"] == 777
    assert captured["price"] is None  # preço de VENDA continua manual no Bling
    assert captured["cost_price"] == 49.0  # custo é enviado a partir de row.custo_bling
    assert captured["_category_name"] == "mala"
