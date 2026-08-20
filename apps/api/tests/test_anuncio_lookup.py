"""anuncio_lookup — buscar UM anúncio pelo ID e vincular só as variações dele.

Cenário real: anúncio novo na Shopee com dezenas de variações. Antes era
preciso rodar "Vincular Automático"/"Sincronizar Todos" da conta inteira; aqui
a busca é por ID (1-2 chamadas) e o vínculo sai só pro que casou por SKU.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Company,
    Integration,
    IntegrationPlatform,
    LinkSyncStatus,
    Marketplace,
    Product,
    ProductLink,
    Store,
    StoreStatus,
    User,
    UserRole,
    UserStatus,
)
from app.security.cipher import encrypt_json
from app.services.anuncio_lookup import (
    lookup_anuncio,
    parece_id_anuncio,
    plataforma_do_id,
    vincular_anuncio,
)


@pytest_asyncio.fixture
async def user(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:an-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"an-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _make_integration(
    db: AsyncSession, user: User, platform: IntegrationPlatform, marketplace: Marketplace
) -> Integration:
    company = Company(razao_social="ACME", apelido=f"acme-{uuid.uuid4().hex[:6]}")
    db.add(company)
    await db.flush()
    store = Store(company_id=company.id, marketplace=marketplace, status=StoreStatus.ACTIVE)
    db.add(store)
    await db.flush()
    creds = (
        {"access_token": "x", "shop_id": 1, "expires_at": 9999999999}
        if platform == IntegrationPlatform.SHOPEE
        else {"access_token": "x", "user_id": 7, "expires_at": 9999999999}
    )
    integ = Integration(
        user_id=user.id,
        store_id=store.id,
        platform=platform,
        name="conta-teste",
        credentials=encrypt_json(creds),
    )
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    return integ


def _fake_base_info(linhas: list[dict]):
    async def _fake(self, item_ids, status_filter):  # noqa: ANN001
        for it in linhas:
            yield it

    return _fake


# ---------------------------------------------------------------- unidade


def test_parece_id_e_plataforma():
    assert parece_id_anuncio("MLB1234567") is True
    assert parece_id_anuncio(" mlb1234567 ") is True
    assert parece_id_anuncio("123456789") is True
    assert parece_id_anuncio("camiseta preta") is False
    assert parece_id_anuncio("1234") is False  # curto demais: é SKU/nome
    assert parece_id_anuncio(None) is False

    assert plataforma_do_id("MLB999999") == IntegrationPlatform.ML
    assert plataforma_do_id("223344556") == IntegrationPlatform.SHOPEE
    assert plataforma_do_id("abc") is None


# ---------------------------------------------------------------- shopee


@pytest.mark.asyncio
async def test_lookup_shopee_agrupa_variacoes_e_classifica(
    db: AsyncSession, user: User, monkeypatch
):
    integ = await _make_integration(
        db, user, IntegrationPlatform.SHOPEE, Marketplace.SHOPEE
    )
    ok = Product(user_id=user.id, sku="sku-ok", name="Camiseta P", stock=12, min_stock=0)
    dup_a = Product(user_id=user.id, sku="sku-dup", name="a", stock=1, min_stock=0)
    dup_b = Product(user_id=user.id, sku="SKU-DUP", name="b", stock=2, min_stock=0)
    db.add_all([ok, dup_a, dup_b])
    await db.commit()

    from app.services.marketplaces import shopee as shopee_mod

    monkeypatch.setattr(
        shopee_mod.ShopeeClient,
        "_fetch_base_info",
        _fake_base_info([
            {"external_id": "555001", "variation_id": "1", "sku": " SKU-OK ",
             "title": "Camiseta - P", "stock": 3},
            {"external_id": "555001", "variation_id": "2", "sku": "sku-dup",
             "title": "Camiseta - M", "stock": 1},
            {"external_id": "555001", "variation_id": "3", "sku": "",
             "title": "Camiseta - G", "stock": 0},
            {"external_id": "555001", "variation_id": "4", "sku": "fantasma",
             "title": "Camiseta - GG", "stock": 5},
        ]),
    )

    dados = await lookup_anuncio(db, user, external_id="555001")

    assert dados["encontrado"] is True
    assert dados["origem"] == "marketplace"
    assert dados["plataforma"] == "shopee"
    assert dados["integration_id"] == str(integ.id)
    assert dados["titulo"] == "Camiseta"  # o " - Variação" some do título do anúncio
    assert dados["resumo"] == {
        "total": 4,
        "prontos": 1,
        "ja_vinculados": 0,
        "sem_sku": 1,
        "sku_ambiguo": 1,
        "sem_produto": 1,
    }
    por_var = {v["variation_id"]: v for v in dados["variacoes"]}
    assert por_var["1"]["estado"] == "pronto"
    assert por_var["1"]["variacao"] == "P"
    assert por_var["1"]["produto_nome"] == "Camiseta P"
    assert por_var["1"]["estoque_bling"] == 12
    assert por_var["1"]["estoque_anuncio"] == 3
    assert por_var["2"]["estado"] == "sku_ambiguo"
    assert por_var["3"]["estado"] == "sem_sku"
    assert por_var["4"]["estado"] == "sem_produto"


@pytest.mark.asyncio
async def test_vincular_anuncio_cria_so_o_que_casou(
    db: AsyncSession, user: User, monkeypatch
):
    integ = await _make_integration(
        db, user, IntegrationPlatform.SHOPEE, Marketplace.SHOPEE
    )
    ok = Product(user_id=user.id, sku="sku-ok", name="ok", stock=9, min_stock=0)
    db.add(ok)
    await db.commit()

    from app.services.marketplaces import shopee as shopee_mod

    monkeypatch.setattr(
        shopee_mod.ShopeeClient,
        "_fetch_base_info",
        _fake_base_info([
            {"external_id": "555002", "variation_id": "1", "sku": "sku-ok",
             "title": "Bermuda - P", "stock": 3},
            {"external_id": "555002", "variation_id": "2", "sku": "fantasma",
             "title": "Bermuda - M", "stock": 1},
        ]),
    )

    out = await vincular_anuncio(
        db, user, external_id="555002", integration_id=integ.id
    )
    assert out["criados"] == 1
    assert len(out["link_ids"]) == 1

    links = (
        await db.execute(
            select(ProductLink).where(ProductLink.external_id == "555002")
        )
    ).scalars().all()
    assert len(links) == 1
    assert links[0].product_id == ok.id
    assert links[0].variation_id == "1"
    assert links[0].external_sku == "sku-ok"
    assert links[0].store_id == integ.store_id
    assert links[0].last_sync_status == LinkSyncStatus.OK

    # Rodar de novo não duplica: a variação já vinculada vira estado "vinculado".
    de_novo = await vincular_anuncio(
        db, user, external_id="555002", integration_id=integ.id
    )
    assert de_novo["criados"] == 0
    assert de_novo["resumo"]["ja_vinculados"] == 1
    assert len(de_novo["link_ids"]) == 1


# -------------------------------------------------------------------- ml


@pytest.mark.asyncio
async def test_lookup_ml_ignora_anuncio_de_outro_vendedor(
    db: AsyncSession, user: User, monkeypatch
):
    await _make_integration(db, user, IntegrationPlatform.ML, Marketplace.ML)

    from app.services.marketplaces import ml as ml_mod

    async def _fake_get_item(self, item_id):  # noqa: ANN001
        # `/items/{id}` é público — sem a conferência de seller_id o anúncio de
        # um concorrente apareceria como se fosse da conta.
        return {
            "id": item_id,
            "seller_id": 999,
            "title": "Anúncio do vizinho",
            "available_quantity": 4,
            "attributes": [{"id": "SELLER_SKU", "value_name": "sku-ok"}],
        }

    monkeypatch.setattr(ml_mod.MercadoLivreClient, "get_item", _fake_get_item)

    dados = await lookup_anuncio(db, user, external_id="MLB123456")
    assert dados["encontrado"] is False
    assert dados["motivo"] == "nao_encontrado"


@pytest.mark.asyncio
async def test_lookup_ml_traz_variacoes_do_dono(db: AsyncSession, user: User, monkeypatch):
    integ = await _make_integration(db, user, IntegrationPlatform.ML, Marketplace.ML)
    p = Product(user_id=user.id, sku="ml-1", name="Caneca", stock=7, min_stock=0)
    db.add(p)
    await db.commit()

    from app.services.marketplaces import ml as ml_mod

    async def _fake_get_item(self, item_id):  # noqa: ANN001
        return {
            "id": item_id,
            "seller_id": 7,  # bate com creds["user_id"]
            "title": "Caneca Térmica",
            "listing_type_id": "gold_special",
            "available_quantity": 5,
            "price": 10.0,
            "status": "active",
            "attributes": [{"id": "SELLER_SKU", "value_name": "ml-1"}],
        }

    monkeypatch.setattr(ml_mod.MercadoLivreClient, "get_item", _fake_get_item)

    dados = await lookup_anuncio(db, user, external_id="mlb987654")
    assert dados["encontrado"] is True
    assert dados["external_id"] == "MLB987654"  # normalizado pra maiúsculo
    assert dados["integration_id"] == str(integ.id)
    assert dados["resumo"]["prontos"] == 1
    assert dados["variacoes"][0]["listing_type"] == "gold_special"


# ------------------------------------------------------------- fallback


@pytest.mark.asyncio
async def test_lookup_cai_no_local_quando_o_canal_nao_responde(
    db: AsyncSession, user: User, monkeypatch
):
    integ = await _make_integration(
        db, user, IntegrationPlatform.SHOPEE, Marketplace.SHOPEE
    )
    p = Product(user_id=user.id, sku="sku-ok", name="ok", stock=4, min_stock=0)
    db.add(p)
    await db.flush()
    db.add(
        ProductLink(
            user_id=user.id,
            product_id=p.id,
            integration_id=integ.id,
            store_id=integ.store_id,
            platform=IntegrationPlatform.SHOPEE,
            external_id="555003",
            variation_id="1",
            external_sku="sku-ok",
            listing_title="Boné",
            stock=2,
        )
    )
    await db.commit()

    from app.services.marketplaces import shopee as shopee_mod

    def _explode(self, item_ids, status_filter):  # noqa: ANN001
        raise RuntimeError("shopee fora do ar")

    monkeypatch.setattr(shopee_mod.ShopeeClient, "_fetch_base_info", _explode)

    dados = await lookup_anuncio(db, user, external_id="555003")
    assert dados["encontrado"] is True
    assert dados["origem"] == "local"  # montado dos vínculos gravados
    assert dados["resumo"]["ja_vinculados"] == 1
    assert dados["variacoes"][0]["estado"] == "vinculado"


@pytest.mark.asyncio
async def test_endpoint_lookup_e_sync_sem_vinculo(
    client, make_user, auth_as, db: AsyncSession, monkeypatch
):
    """A rota devolve o anúncio agrupado; sem SKU casado, o sync recusa em vez
    de rodar um push vazio."""
    u = await make_user(role=UserRole.ADMIN)
    auth_as(u)
    integ = await _make_integration(db, u, IntegrationPlatform.SHOPEE, Marketplace.SHOPEE)

    from app.services.marketplaces import shopee as shopee_mod

    monkeypatch.setattr(
        shopee_mod.ShopeeClient,
        "_fetch_base_info",
        _fake_base_info([
            {"external_id": "556001", "variation_id": "1", "sku": "nao-existe",
             "title": "Tênis - 38", "stock": 2},
        ]),
    )

    r = await client.get("/api/anuncio/lookup", params={"id": "556001"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["encontrado"] is True
    assert body["conta"] == "conta-teste"
    assert body["resumo"]["sem_produto"] == 1
    assert body["variacoes"][0]["estado"] == "sem_produto"

    r2 = await client.post(
        "/api/anuncio/sync",
        json={"external_id": "556001", "integration_id": str(integ.id), "vincular": True},
    )
    assert r2.status_code == 400
    assert r2.json()["detail"]["code"] == "anuncio_sem_vinculos"

    r3 = await client.post(
        "/api/anuncio/sync",
        json={"external_id": "000000", "integration_id": str(integ.id)},
    )
    assert r3.status_code == 404
    assert r3.json()["detail"]["code"] == "anuncio_nao_encontrado"


@pytest.mark.asyncio
async def test_lookup_id_desconhecido_nao_encontra(db: AsyncSession, user: User, monkeypatch):
    await _make_integration(db, user, IntegrationPlatform.SHOPEE, Marketplace.SHOPEE)

    from app.services.marketplaces import shopee as shopee_mod

    monkeypatch.setattr(shopee_mod.ShopeeClient, "_fetch_base_info", _fake_base_info([]))

    dados = await lookup_anuncio(db, user, external_id="555999")
    assert dados["encontrado"] is False
    assert dados["motivo"] == "nao_encontrado"
    assert dados["plataforma"] == "shopee"
