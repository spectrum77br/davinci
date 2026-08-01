"""Endpoint POST /api/nf-cadastro/faturamento/conferir-frete/pedido.

"Fio automático" do confere-frete: dado só o nº do pedido, resolve
CEP/caixa/frete projetado (como o /auto) E cota no Melhor Envio (como o
/conferir-frete) numa chamada só, devolvendo a decisão `libera` + o contexto.
As camadas puras já são testadas (test_melhor_envio.py, test_nf_frete_auto.py);
aqui cobre o wire ponta a ponta.
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
from app.services.melhor_envio import Cotacao, MelhorEnvioClient

_ROTA = "/api/nf-cadastro/faturamento/conferir-frete/pedido"

_COTACOES = [
    Cotacao(1, "PAC", "Correios", Decimal("24.50"), 6, None),
    Cotacao(2, "SEDEX", "Correios", Decimal("39.90"), 2, None),
    Cotacao(17, ".Package", "Jadlog", None, None, "indisponível"),
]


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
    um pedido com CEP destino e um item mapeado nesse slot (frete projetado 30)."""
    root = Segment(slug="celular", name="Celular", parent_id=None, sort_order=0)
    db.add(root)
    await db.flush()
    leaf = Segment(
        slug="celular-normal",
        name="Normal",
        parent_id=root.id,
        sort_order=1,
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


def _patch_bling_dims(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_client(session):
        class _C:
            async def get_product(self, pid):
                return {
                    "dimensoes": {"largura": "20", "altura": "10", "profundidade": "15"},
                    "pesoBruto": "1.2",
                }

        return _C()

    monkeypatch.setattr(
        nf_frete_auto.nf_emissao_gerar, "_bling_client_opt", _fake_client
    )


@pytest.mark.asyncio
async def test_pedido_resolve_e_cota_libera(
    client: AsyncClient,
    admin: User,
    auth_as: Callable[[User | None], None],
    pedido_setup: dict,
    monkeypatch: pytest.MonkeyPatch,
):
    auth_as(admin)
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "nf_origem_cep", "13400853", raising=False)
    _patch_bling_dims(monkeypatch)

    async def _fake(self, **kwargs):
        # cota com o CEP origem do .env e o destino do pedido
        assert kwargs["from_cep"] == "13400853"
        assert kwargs["to_cep"] == "01310100"
        return _COTACOES

    monkeypatch.setattr(MelhorEnvioClient, "calcular_frete", _fake)

    r = await client.post(_ROTA, json={"pedido_bling": "990001"})
    assert r.status_code == 200
    data = r.json()
    assert data["pedido_bling"] == "990001"
    assert data["from_cep"] == "13400853"
    assert data["to_cep"] == "01310100"
    assert data["plataforma"] == "Amazon"
    assert data["conta"] == "conta-amz"
    assert data["nome_destinatario"] == "Fulano"
    assert data["prefill_ok"] is True
    conf = data["conferencia"]
    assert conf["libera"] is True
    assert conf["motivo"] == "dentro_do_projetado"
    assert conf["menor_frete"] == "24.50"
    assert conf["frete_projetado"] == "30.00"
    assert len(conf["cotacoes"]) == 3


@pytest.mark.asyncio
async def test_pedido_sem_pricing_prefill_ok_false(
    db: AsyncSession,
    client: AsyncClient,
    admin: User,
    auth_as: Callable[[User | None], None],
    monkeypatch: pytest.MonkeyPatch,
):
    """Loja sem conta na Tabela de Preços → frete projetado None → prefill_ok
    False; a cotação roda mas a decisão fica sem base (motivo sem_frete)."""
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
        return None

    monkeypatch.setattr(
        nf_frete_auto.nf_emissao_gerar, "_bling_client_opt", _fake_client
    )

    async def _fake(self, **kwargs):
        return _COTACOES

    monkeypatch.setattr(MelhorEnvioClient, "calcular_frete", _fake)

    r = await client.post(_ROTA, json={"pedido_bling": "990002"})
    assert r.status_code == 200
    data = r.json()
    assert data["prefill_ok"] is False
    assert data["conferencia"]["frete_projetado"] is None
    assert any("projetado" in a.lower() for a in data["avisos"])


@pytest.mark.asyncio
async def test_pedido_sem_origem_cep_400(
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
async def test_pedido_inexistente_404(
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
async def test_pedido_exige_auth(client: AsyncClient):
    r = await client.post(_ROTA, json={"pedido_bling": "990001"})
    assert r.status_code in (401, 403)
