"""Fase 2 da aba Kit: criação de produto composto no Bling.

Cobertura:
  * happy path: kit 2 componentes com bling_product_id resolvidos → 'sent'
  * missing_component: 1 componente sem bling_product_id → 'error'
  * idempotência: rodar 2x com bling_product_id já setado → no-op
  * parse_kit_variation: vários formatos
  * desmarcar não chama Bling (cobertura na test_importacao_kit.py)

BlingClient é mockado via monkeypatch.setattr — não bate na API real.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ImportKitBase,
    ImportKitMark,
    ImportKitVariation,
    Integration,
    IntegrationPlatform,
    Product,
    User,
    UserRole,
    UserStatus,
)
from app.security.cipher import encrypt_json
from app.services.bling_kit_create import create_bling_kit_for_mark
from app.services.importacao_naming import (
    build_kit_pricing_sku,
    generate_kit_name,
    parse_kit_variation,
)

# ── parse_kit_variation (pure function — sem fixtures) ──────────────


def test_parse_simple_size():
    assert parse_kit_variation("8") == (["8"], [])


def test_parse_two_sizes():
    assert parse_kit_variation("8+18") == (["8", "18"], [])


def test_parse_comma_separated():
    assert parse_kit_variation("12,14,16") == (["12", "14", "16"], [])


def test_parse_kit_with_accessories():
    sizes, acc = parse_kit_variation("12+20+24+a075+bp003+a076")
    assert sizes == ["12", "20", "24"]
    assert acc == ["a075", "bp003", "a076"]


def test_parse_empty_returns_empty():
    assert parse_kit_variation("") == ([], [])


def test_parse_whitespace_pieces_are_skipped():
    assert parse_kit_variation(" 8 + + 18 ") == (["8", "18"], [])


# ── generate_kit_name + build_kit_pricing_sku — convenção '.' ─────


def test_generate_kit_name_uses_dot_between_sizes():
    """SKU convention: '.' separa tamanhos, '+' apenas pra acessórios."""
    assert generate_kit_name(
        "M5 mista", "b045", "8+18", "preto",
    ) == "Kit Mala M5 mista tamanhos 8.18 - preto"


def test_generate_kit_name_three_sizes():
    assert generate_kit_name(
        "M5 mista", "b045", "8+12+20+24", "preto",
    ) == "Kit Mala M5 mista tamanhos 8.12.20.24 - preto"


def test_generate_kit_name_with_accessories_keeps_plus():
    """Acessórios continuam separados por '+' (após os tamanhos com '.')."""
    name = generate_kit_name(
        "M5 mista", "b045", "12+20+24+a075+bp002+a076", "preto",
    )
    assert "tamanhos 12.20.24" in name
    assert "+ a075 + bp002 + a076" in name
    assert " - preto" in name


def test_build_kit_pricing_sku_aligned_with_name():
    """O SKU usa o mesmo separador '.' que aparece no nome."""
    assert build_kit_pricing_sku("b045", "8+18") == "b045.8.18"
    assert build_kit_pricing_sku(
        "b045", "12+20+24+a075+bp002+a076",
    ) == "b045.12.20.24+a075+bp002+a076"


# ── Worker tests (mocked Bling) ──────────────────────────────────────


@pytest_asyncio.fixture
async def bling_integration(db: AsyncSession) -> Integration:
    """Integration row pra Bling — credentials criptografadas mas o
    cliente é mockado, então o valor não importa. Integration.user_id
    é NOT NULL, então criamos um user dummy primeiro."""
    owner = User(
        open_id=f"email:owner-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"owner-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
        permissions={},
    )
    db.add(owner)
    await db.commit()
    await db.refresh(owner)
    integ = Integration(
        user_id=owner.id,
        platform=IntegrationPlatform.BLING,
        name="Bling Test",
        credentials=encrypt_json({
            "access_token": "tok",
            "refresh_token": "ref",
            "client_id": "cid",
            "client_secret": "csec",
            "expires_at": int(datetime.now(UTC).timestamp()) + 3600,
        }),
    )
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    return integ


@pytest_asyncio.fixture
async def kit_setup(db: AsyncSession) -> dict[str, Any]:
    """Cria base + variation + componentes em `products` com bling_product_id."""
    v = ImportKitVariation(code="8+18", label="8+18", ordem=1, highlight=False)
    b = ImportKitBase(modelo_bling="M5 mista", sku_base="b045", cor="preto", ordem=1)
    # Products requer user_id (NOT NULL)
    owner = User(
        open_id=f"email:po-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"po-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
        permissions={},
    )
    db.add_all([v, b, owner])
    await db.commit()
    await db.refresh(v)
    await db.refresh(b)
    await db.refresh(owner)

    # Componentes: b045.8 e b045.18 em `products`
    p1 = Product(
        user_id=owner.id, sku="b045.8",
        name="Mala b045 tamanho 8", bling_product_id=1001,
    )
    p2 = Product(
        user_id=owner.id, sku="b045.18",
        name="Mala b045 tamanho 18", bling_product_id=1002,
    )
    db.add_all([p1, p2])
    await db.commit()

    mark = ImportKitMark(
        base_id=b.id, variation_id=v.id, bling_sync_status="pending",
    )
    db.add(mark)
    await db.commit()
    await db.refresh(mark)
    return {"base": b, "variation": v, "mark": mark, "p1": p1, "p2": p2}


def _install_bling_mock(monkeypatch, *, return_id: int = 9999, raise_exc: Exception | None = None):
    """Substitui BlingClient.create_product e find_or_create_category
    por mocks que devolvem dados controlados."""

    async def fake_find_or_create_category(self, name):
        return 555

    async def fake_find_contato_id_by_name(self, name):
        return 16980149177

    async def fake_create_product(self, **kwargs):
        if raise_exc is not None:
            raise raise_exc
        # Echo do shape esperado pelo serviço — `data["id"]`.
        return {"id": return_id, "echo": kwargs}

    async def fake_link_supplier(self, **kwargs):
        return {"id": 1}

    async def fake_update_product_estrutura(self, **kwargs):
        return {}

    from app.services.marketplaces.bling import BlingClient
    monkeypatch.setattr(BlingClient, "find_or_create_category", fake_find_or_create_category)
    monkeypatch.setattr(BlingClient, "find_contato_id_by_name", fake_find_contato_id_by_name)
    monkeypatch.setattr(BlingClient, "create_product", fake_create_product)
    monkeypatch.setattr(BlingClient, "link_supplier_to_product", fake_link_supplier)
    monkeypatch.setattr(BlingClient, "update_product_estrutura", fake_update_product_estrutura)


@pytest.mark.asyncio
async def test_create_kit_happy_path(
    db: AsyncSession,
    bling_integration: Integration,
    kit_setup: dict[str, Any],
    monkeypatch,
):
    """2 componentes, ambos com bling_product_id → cria, status='sent'."""
    _install_bling_mock(monkeypatch, return_id=88888)
    result = await create_bling_kit_for_mark(kit_setup["mark"].id)
    assert result["ok"] is True
    assert result["bling_product_id"] == 88888

    # Verificar DB: mark atualizada
    await db.refresh(kit_setup["mark"])
    m = kit_setup["mark"]
    assert m.bling_product_id == 88888
    assert m.bling_sync_status == "sent"
    assert m.bling_sync_error is None
    assert m.bling_sync_done_at is not None


@pytest.mark.asyncio
async def test_create_kit_missing_component_bling_id_errors(
    db: AsyncSession,
    bling_integration: Integration,
    kit_setup: dict[str, Any],
    monkeypatch,
):
    """1 componente sem bling_product_id → status='error', error msg detalha."""
    _install_bling_mock(monkeypatch)
    # Apaga o bling_product_id do componente p1
    kit_setup["p1"].bling_product_id = None
    db.add(kit_setup["p1"])
    await db.commit()

    result = await create_bling_kit_for_mark(kit_setup["mark"].id)
    assert result["ok"] is False
    assert "missing_component" in result["error"]

    await db.refresh(kit_setup["mark"])
    m = kit_setup["mark"]
    assert m.bling_product_id is None
    assert m.bling_sync_status == "error"
    assert "b045.8" in (m.bling_sync_error or "")
    assert m.bling_sync_attempted_at is not None


@pytest.mark.asyncio
async def test_create_kit_idempotent_when_already_synced(
    db: AsyncSession,
    bling_integration: Integration,
    kit_setup: dict[str, Any],
    monkeypatch,
):
    """Mark já com bling_product_id → worker pula sem chamar Bling."""
    # Pré-setar
    mark = kit_setup["mark"]
    mark.bling_product_id = 77777
    mark.bling_sync_status = "sent"
    db.add(mark)
    await db.commit()

    bling_calls = {"create_product": 0}

    from app.services.marketplaces.bling import BlingClient

    async def fake_create(self, **kwargs):
        bling_calls["create_product"] += 1
        return {"id": 99999}

    async def fake_cat(self, name):
        return 1

    monkeypatch.setattr(BlingClient, "create_product", fake_create)
    monkeypatch.setattr(BlingClient, "find_or_create_category", fake_cat)

    result = await create_bling_kit_for_mark(mark.id)
    assert result["ok"] is True
    assert result.get("skipped") is True
    assert result["bling_product_id"] == 77777
    # Bling NÃO foi chamado
    assert bling_calls["create_product"] == 0


@pytest.mark.asyncio
async def test_create_kit_no_bling_integration_errors(
    db: AsyncSession,
    kit_setup: dict[str, Any],
    monkeypatch,
):
    """Sem Integration de Bling no DB → status='error'."""
    # Não há `bling_integration` fixture aqui, então não tem integration.
    _install_bling_mock(monkeypatch)
    result = await create_bling_kit_for_mark(kit_setup["mark"].id)
    assert result["ok"] is False
    assert result["error"] == "no_bling_integration"

    await db.refresh(kit_setup["mark"])
    assert kit_setup["mark"].bling_sync_status == "error"


@pytest.mark.asyncio
async def test_create_kit_sums_components_cost(
    db: AsyncSession,
    bling_integration: Integration,
    kit_setup: dict[str, Any],
    monkeypatch,
):
    """Custo do composto = soma dos bling_cost_price dos componentes,
    enviado via link_supplier_to_product (não pelo create).
    Companion: cada componente recebe link_supplier_to_product separado
    com SEU PRÓPRIO custo — Bling V3 zera o precoCusto dos componentes
    ao criar o kit, então re-aplicamos pra restaurar."""
    from decimal import Decimal
    # Set custos nos componentes b045.8 e b045.18
    kit_setup["p1"].bling_cost_price = Decimal("30.00")
    kit_setup["p2"].bling_cost_price = Decimal("70.50")
    db.add(kit_setup["p1"])
    db.add(kit_setup["p2"])
    await db.commit()

    captured: dict[str, Any] = {}
    link_calls: list[dict[str, Any]] = []
    from app.services.marketplaces.bling import BlingClient

    async def fake_create(self, **kwargs):
        captured.update(kwargs)
        return {"id": 88888}

    async def fake_cat(self, name):
        return 555

    async def fake_supplier(self, name):
        return 16980149177

    async def fake_link(self, **kwargs):
        link_calls.append(dict(kwargs))
        return {"id": len(link_calls)}

    monkeypatch.setattr(BlingClient, "create_product", fake_create)
    monkeypatch.setattr(BlingClient, "find_or_create_category", fake_cat)
    monkeypatch.setattr(BlingClient, "find_contato_id_by_name", fake_supplier)
    monkeypatch.setattr(BlingClient, "link_supplier_to_product", fake_link)

    await create_bling_kit_for_mark(kit_setup["mark"].id)
    assert "cost_price" not in captured  # custo não vai no create

    # Chamada 1: kit recebe o custo somado.
    assert link_calls[0]["product_id"] == 88888
    assert link_calls[0]["supplier_id"] == 16980149177
    assert link_calls[0]["cost_price"] == pytest.approx(100.50)

    # Chamadas 2-N: 1 por componente, com SEU custo (restore após Bling
    # zerar). bling_product_id dos componentes vem de kit_setup (1001/1002),
    # custos 30.00 e 70.50.
    by_product = {c["product_id"]: c for c in link_calls[1:]}
    assert set(by_product.keys()) == {1001, 1002}
    custos_componentes = sorted(c["cost_price"] for c in by_product.values())
    assert custos_componentes == [pytest.approx(30.00), pytest.approx(70.50)]
    # Todos com o mesmo fornecedor (default).
    for c in by_product.values():
        assert c["supplier_id"] == 16980149177


@pytest.mark.asyncio
async def test_create_kit_omits_cost_when_components_have_no_price(
    db: AsyncSession,
    bling_integration: Integration,
    kit_setup: dict[str, Any],
    monkeypatch,
):
    """Componentes sem bling_cost_price → custo 0 → link_supplier NÃO
    é chamado (e o create nunca recebe custo)."""
    captured: dict[str, Any] = {}
    link_calls = {"n": 0}
    from app.services.marketplaces.bling import BlingClient

    async def fake_create(self, **kwargs):
        captured.update(kwargs)
        return {"id": 88888}

    async def fake_cat(self, name):
        return 555

    async def fake_link(self, **kwargs):
        link_calls["n"] += 1
        return {"id": 1}

    monkeypatch.setattr(BlingClient, "create_product", fake_create)
    monkeypatch.setattr(BlingClient, "find_or_create_category", fake_cat)
    monkeypatch.setattr(BlingClient, "link_supplier_to_product", fake_link)

    await create_bling_kit_for_mark(kit_setup["mark"].id)
    assert "cost_price" not in captured
    assert link_calls["n"] == 0


@pytest.mark.asyncio
async def test_create_kit_passes_correct_estrutura_to_bling(
    db: AsyncSession,
    bling_integration: Integration,
    kit_setup: dict[str, Any],
    monkeypatch,
):
    """Verifica que o POST /produtos recebe formato='E' + estrutura
    completa (Bling V3 rev. 2026-06 exige no POST). PUT /produtos/
    estruturas continua sendo chamado como reforço defensivo —
    mesma estrutura."""
    captured_create: dict[str, Any] = {}
    captured_estrutura: dict[str, Any] = {}

    from app.services.marketplaces.bling import BlingClient

    async def fake_create(self, **kwargs):
        captured_create.update(kwargs)
        return {"id": 88888}

    async def fake_estrutura(self, **kwargs):
        captured_estrutura.update(kwargs)
        return {}

    async def fake_cat(self, name):
        return 555

    monkeypatch.setattr(BlingClient, "create_product", fake_create)
    monkeypatch.setattr(BlingClient, "update_product_estrutura", fake_estrutura)
    monkeypatch.setattr(BlingClient, "find_or_create_category", fake_cat)

    await create_bling_kit_for_mark(kit_setup["mark"].id)

    # POST /produtos: formato + estrutura completa.
    assert captured_create["formato"] == "E"
    assert captured_create["sku"] == "b045.8.18"
    assert captured_create["category_id"] == 555
    est_post = captured_create["estrutura"]
    assert est_post["tipoEstoque"] == "V"
    assert est_post["lancamentoEstoque"] == "M"
    comp_ids_post = sorted(c["produto"]["id"] for c in est_post["componentes"])
    assert comp_ids_post == [1001, 1002]
    for c in est_post["componentes"]:
        assert c["quantidade"] == 1

    # PUT /produtos/estruturas/{id}: chamado como reforço, mesma estrutura.
    assert captured_estrutura["product_id"] == 88888
    est_put = captured_estrutura["estrutura"]
    assert est_put["tipoEstoque"] == "V"
    assert est_put["lancamentoEstoque"] == "M"
    comp_ids_put = sorted(c["produto"]["id"] for c in est_put["componentes"])
    assert comp_ids_put == [1001, 1002]
