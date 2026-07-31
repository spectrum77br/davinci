"""Endpoint POST /api/nf-cadastro/faturamento/conferir-frete/auto.

Prefill do confere-frete a partir do pedido: CEP destino de bling_orders,
dimensões do produto (Bling, monkeypatch) e frete projetado da Tabela de Preços.
Best-effort — frete None + aviso quando não resolve.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingOrder,
    PricingAccount,
    PricingPlatform,
    PricingProduct,
    Segment,
    StoreInfo,
    User,
    UserRole,
    UserStatus,
)
from app.services import nf_frete_auto

_ROTA = "/api/nf-cadastro/faturamento/conferir-frete/auto"


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
async def pedido_setup(db: AsyncSession, admin: User) -> dict:
    """Loja com conta na Tabela de Preços (root celular slot 2 = shipping2) +
    um pedido com CEP destino e um item mapeado nesse slot."""
    root = Segment(slug="celular", name="Celular", parent_id=None, sort_order=0)
    db.add(root)
    await db.flush()
    leaf = Segment(
        slug="celular-normal",
        name="Normal",
        parent_id=root.id,
        sort_order=1,  # product_type = sort_order + 1 = 2 → shipping2
    )
    db.add(leaf)
    await db.flush()

    store = StoreInfo(
        user_id=admin.id,
        platform="amazon",
        account_name="conta-amz",
        bling_store_id="loja77",
    )
    db.add(store)
    await db.flush()

    acc = PricingAccount(
        user_id=admin.id,
        name="conta-amz",
        platform=PricingPlatform.AMAZON,
        segment_id=root.id,
        store_info_id=store.id,
        shipping2=Decimal("30.00"),
    )
    db.add(acc)
    db.add(
        PricingProduct(
            user_id=admin.id,
            sku="sku-1",
            name="Produto 1",
            segment_id=leaf.id,
        )
    )
    db.add(
        BlingOrder(
            numero="990001",
            loja="loja77",
            item_index=0,
            item_produto_id=555,
            item_codigo="sku-1",
            item_descricao="Produto 1",
            item_quantidade=2,
            cep_destino="01310100",
            nome_destinatario="Fulano",
        )
    )
    await db.commit()
    return {"store_id": store.id}


@pytest.mark.asyncio
async def test_auto_resolve_cep_dims_e_frete(
    client: AsyncClient,
    admin: User,
    auth_as: Callable[[User | None], None],
    pedido_setup: dict,
    monkeypatch: pytest.MonkeyPatch,
):
    auth_as(admin)
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "nf_origem_cep", "13400853", raising=False)

    async def _fake_client(session):
        class _C:
            async def get_product(self, pid):
                return {
                    "dimensoes": {"largura": "20", "altura": "10", "profundidade": "15"},
                    "pesoBruto": "1.2",
                }

        return _C()

    monkeypatch.setattr(nf_frete_auto.nf_emissao_gerar, "_bling_client_opt", _fake_client)

    r = await client.post(_ROTA, json={"pedido_bling": "990001"})
    assert r.status_code == 200
    data = r.json()
    assert data["from_cep"] == "13400853"
    assert data["to_cep"] == "01310100"
    assert data["frete_projetado"] == "30.00"
    assert data["plataforma"] == "Amazon"
    assert data["conta"] == "conta-amz"
    assert len(data["produtos"]) == 1
    p = data["produtos"][0]
    assert p["width"] == "20" and p["height"] == "10" and p["length"] == "15"
    assert p["weight"] == "1.2" and p["quantity"] == 2
    assert data["avisos"] == []


@pytest.mark.asyncio
async def test_auto_sem_dimensao_usa_fallback_e_avisa(
    client: AsyncClient,
    admin: User,
    auth_as: Callable[[User | None], None],
    pedido_setup: dict,
    monkeypatch: pytest.MonkeyPatch,
):
    auth_as(admin)
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "nf_origem_cep", "13400853", raising=False)

    async def _fake_client(session):
        class _C:
            async def get_product(self, pid):
                return {}  # sem dimensoes

        return _C()

    monkeypatch.setattr(nf_frete_auto.nf_emissao_gerar, "_bling_client_opt", _fake_client)

    r = await client.post(_ROTA, json={"pedido_bling": "990001"})
    assert r.status_code == 200
    data = r.json()
    p = data["produtos"][0]
    # caixa fallback
    assert p["width"] == "20" and p["height"] == "15" and p["length"] == "10"
    assert any("dimens" in a.lower() for a in data["avisos"])
    # frete ainda resolve (não depende das dims)
    assert data["frete_projetado"] == "30.00"


@pytest.mark.asyncio
async def test_auto_sem_origem_cep_400(
    client: AsyncClient,
    admin: User,
    auth_as: Callable[[User | None], None],
    pedido_setup: dict,
    monkeypatch: pytest.MonkeyPatch,
):
    auth_as(admin)
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "nf_origem_cep", "", raising=False)
    r = await client.post(_ROTA, json={"pedido_bling": "990001"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "nf_origem_cep_missing"


@pytest.mark.asyncio
async def test_auto_pedido_inexistente_404(
    client: AsyncClient,
    admin: User,
    auth_as: Callable[[User | None], None],
    monkeypatch: pytest.MonkeyPatch,
):
    auth_as(admin)
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "nf_origem_cep", "13400853", raising=False)
    r = await client.post(_ROTA, json={"pedido_bling": "nao-existe"})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "nf_pedido_nao_encontrado"


@pytest.mark.asyncio
async def test_auto_sem_pricing_frete_none_com_aviso(
    db: AsyncSession,
    client: AsyncClient,
    admin: User,
    auth_as: Callable[[User | None], None],
    monkeypatch: pytest.MonkeyPatch,
):
    """Pedido de loja sem conta na Tabela de Preços → frete None + aviso, nunca
    chuta o projetado."""
    store = StoreInfo(
        user_id=admin.id,
        platform="amazon",
        account_name="conta-sem-preco",
        bling_store_id="loja88",
    )
    db.add(store)
    db.add(
        BlingOrder(
            numero="990002",
            loja="loja88",
            item_index=0,
            item_produto_id=999,
            item_codigo="sku-x",
            item_descricao="Produto X",
            item_quantidade=1,
            cep_destino="20040002",
            nome_destinatario="Beltrano",
        )
    )
    await db.commit()

    auth_as(admin)
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "nf_origem_cep", "13400853", raising=False)

    async def _fake_client(session):
        return None  # sem integração Bling

    monkeypatch.setattr(nf_frete_auto.nf_emissao_gerar, "_bling_client_opt", _fake_client)

    r = await client.post(_ROTA, json={"pedido_bling": "990002"})
    assert r.status_code == 200
    data = r.json()
    assert data["frete_projetado"] is None
    assert any("projetado" in a.lower() for a in data["avisos"])
    # sem integração Bling → aviso de caixa padrão
    assert any("padrão" in a.lower() for a in data["avisos"])


@pytest.mark.asyncio
async def test_auto_exige_auth(client: AsyncClient):
    r = await client.post(_ROTA, json={"pedido_bling": "990001"})
    assert r.status_code in (401, 403)
