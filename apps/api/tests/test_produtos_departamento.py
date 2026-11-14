"""Produtos e Anúncios por departamento (Organização do DaVinci, 13/11/2026, item 2).

Celular (com o Catálogo), Mala, Eletro e Sem departamento — pela raiz do
segmento do produto; o anúncio vale pelo produto vinculado e, sem produto,
cai em Sem departamento. O conftest usa create_all (não roda o seed das
raízes da 0023/0024), então cada teste cria as raízes que usa.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Company,
    Integration,
    IntegrationPlatform,
    Listing,
    ListingStatus,
    Marketplace,
    Product,
    ProductLink,
    Segment,
    Store,
    StoreStatus,
    User,
    UserRole,
)
from app.security.cipher import encrypt_json
from app.services import departamento as dep_svc

TUDO = {
    "produtos": {"view": True, "edit": False, "delete": False},
    "anuncios": {"view": True, "edit": False, "delete": False},
}


async def _segmentos(db: AsyncSession) -> dict[str, Segment]:
    """Raízes com os slugs de produção + uma folha em cada (e uma raiz que
    não é departamento, para provar que ela cai em Sem departamento)."""
    out: dict[str, Segment] = {}
    for slug, nome in (
        ("celular", "Celular"),
        ("catalogo", "Catálogo"),
        ("mala", "Mala"),
        ("eletro", "Eletro"),
        ("outra", "Outra"),
    ):
        raiz = Segment(name=nome, slug=slug)
        db.add(raiz)
        await db.flush()
        folha = Segment(name=f"{nome} folha", slug=f"{slug}-folha", parent_id=raiz.id)
        db.add(folha)
        await db.flush()
        out[slug] = folha
        out[f"{slug}-raiz"] = raiz
    # Folha desativada continua no departamento da raiz (ex.: Queima de estoque).
    inativa = Segment(name="Queima", slug="queima", parent_id=out["mala-raiz"].id, active=False)
    db.add(inativa)
    await db.flush()
    out["mala-inativa"] = inativa
    await db.commit()
    return out


async def _integracao(db: AsyncSession, user: User) -> Integration:
    company = Company(razao_social="ACME", apelido=f"acme-{uuid.uuid4().hex[:4]}")
    db.add(company)
    await db.flush()
    store = Store(company_id=company.id, marketplace=Marketplace.SHOPEE, status=StoreStatus.ACTIVE)
    db.add(store)
    await db.flush()
    integ = Integration(
        user_id=user.id,
        store_id=store.id,
        platform=IntegrationPlatform.SHOPEE,
        name="acme-shopee",
        credentials=encrypt_json({"access_token": "x"}),
    )
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    return integ


async def _cenario(db: AsyncSession, user: User) -> dict[str, Product]:
    """Um produto por folha, um na raiz Celular, um sem segmento; um anúncio por
    produto e um anúncio sem produto."""
    seg = await _segmentos(db)
    integ = await _integracao(db, user)
    produtos: dict[str, Product] = {}
    for chave, segment_id in (
        ("celular", seg["celular"].id),
        ("celular-raiz", seg["celular-raiz"].id),
        ("catalogo", seg["catalogo"].id),
        ("mala", seg["mala"].id),
        ("mala-inativa", seg["mala-inativa"].id),
        ("eletro", seg["eletro"].id),
        ("outra", seg["outra"].id),
        ("sem", None),
    ):
        p = Product(
            user_id=user.id,
            sku=f"sku-{chave}",
            name=f"Produto {chave}",
            stock=0,
            min_stock=0,
            segment_id=segment_id,
        )
        db.add(p)
        await db.flush()
        produtos[chave] = p
        db.add(
            Listing(
                user_id=user.id,
                integration_id=integ.id,
                platform=IntegrationPlatform.SHOPEE,
                external_id=f"ext-{chave}",
                sku=p.sku,
                title=f"Anúncio {chave}",
                status=ListingStatus.UNDER_REVIEW if chave == "eletro" else ListingStatus.ACTIVE,
                product_id=p.id,
            )
        )
    db.add(
        Listing(
            user_id=user.id,
            integration_id=integ.id,
            platform=IntegrationPlatform.SHOPEE,
            external_id="ext-solto",
            sku="solto",
            title="Anúncio sem produto",
            status=ListingStatus.PAUSED,
            product_id=None,
        )
    )
    await db.commit()
    return produtos


@pytest.mark.asyncio
async def test_produtos_por_departamento(client, auth_as, db, make_user):
    user = await make_user(permissions=TUDO)
    await _cenario(db, user)
    auth_as(user)

    async def skus(dep: str) -> set[str]:
        r = await client.get(f"/api/products?departamento={dep}&page_size=200")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == len(body["items"])
        return {i["sku"] for i in body["items"]}

    # Catálogo entra em Celular; produto na própria raiz também.
    assert await skus("celular") == {"sku-celular", "sku-celular-raiz", "sku-catalogo"}
    assert await skus("mala") == {"sku-mala", "sku-mala-inativa"}
    assert await skus("eletro") == {"sku-eletro"}
    # Sem segmento e raiz que não é departamento: Sem departamento.
    assert await skus("sem") == {"sku-sem", "sku-outra"}
    # Sem o parâmetro, nada muda.
    r = await client.get("/api/products?page_size=200")
    assert r.json()["total"] == 8
    r = await client.get("/api/products?departamento=xyz")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_departamento"


@pytest.mark.asyncio
async def test_anuncios_por_departamento_e_resumo(client, auth_as, db, make_user):
    user = await make_user(permissions=TUDO)
    await _cenario(db, user)
    auth_as(user)

    r = await client.get("/api/listings?departamento=sem&page_size=200")
    assert r.status_code == 200, r.text
    body = r.json()
    # Sem produto vinculado + produto sem segmento + raiz desconhecida.
    assert {i["title"] for i in body["items"]} == {
        "Anúncio sem produto",
        "Anúncio sem",
        "Anúncio outra",
    }
    assert body["resumo"] == {"total": 3, "sem_produto": 1, "ativos": 2, "em_revisao": 0}

    r = await client.get("/api/listings?departamento=celular&page_size=200")
    body = r.json()
    assert {i["title"] for i in body["items"]} == {
        "Anúncio celular",
        "Anúncio celular-raiz",
        "Anúncio catalogo",
    }
    assert body["resumo"] == {"total": 3, "sem_produto": 0, "ativos": 3, "em_revisao": 0}

    # Os cartões contam o departamento inteiro; a lista e o total, os filtros.
    r = await client.get("/api/listings?departamento=eletro&status=active")
    body = r.json()
    assert body["total"] == 0
    assert body["resumo"] == {"total": 1, "sem_produto": 0, "ativos": 0, "em_revisao": 1}

    # Sem departamento: o resumo é de todos (antes o front contava só a página).
    r = await client.get("/api/listings?page_size=1")
    body = r.json()
    assert len(body["items"]) == 1
    assert body["total"] == 9
    assert body["resumo"] == {"total": 9, "sem_produto": 1, "ativos": 7, "em_revisao": 1}

    r = await client.get("/api/listings?departamento=nada")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_vincular_tira_o_anuncio_de_sem_departamento(client, auth_as, db, make_user):
    user = await make_user(permissions=TUDO)
    produtos = await _cenario(db, user)
    auth_as(user)
    solto = next(
        i
        for i in (await client.get("/api/listings?departamento=sem")).json()["items"]
        if i["title"] == "Anúncio sem produto"
    )
    db_listing = await db.get(Listing, uuid.UUID(solto["id"]))
    db_listing.product_id = produtos["mala"].id
    await db.commit()
    titulos = {
        i["title"] for i in (await client.get("/api/listings?departamento=sem")).json()["items"]
    }
    assert "Anúncio sem produto" not in titulos
    titulos = {
        i["title"] for i in (await client.get("/api/listings?departamento=mala")).json()["items"]
    }
    assert "Anúncio sem produto" in titulos


@pytest.mark.asyncio
async def test_contagem_por_departamento(client, auth_as, db, make_user):
    user = await make_user(permissions=TUDO)
    await _cenario(db, user)
    auth_as(user)
    r = await client.get("/api/products/departamentos")
    assert r.status_code == 200, r.text
    assert r.json() == {
        "produtos": {"celular": 3, "mala": 2, "eletro": 1, "sem": 2},
        "anuncios": {"celular": 3, "mala": 2, "eletro": 1, "sem": 3},
    }


@pytest.mark.asyncio
async def test_contagem_so_do_que_a_pessoa_pode_ver(client, auth_as, db, make_user):
    dono = await make_user(permissions=TUDO)
    await _cenario(db, dono)

    so_anuncios = await make_user(permissions={"anuncios": {"view": True}})
    auth_as(so_anuncios)
    r = await client.get("/api/products/departamentos")
    assert r.status_code == 200, r.text
    assert r.json()["produtos"] is None
    assert r.json()["anuncios"]["sem"] == 3

    so_produtos = await make_user(permissions={"produtos": {"view": True}})
    auth_as(so_produtos)
    r = await client.get("/api/products/departamentos")
    assert r.json()["anuncios"] is None
    assert r.json()["produtos"]["celular"] == 3

    ninguem = await make_user(permissions={"margem": {"view": True}})
    auth_as(ninguem)
    r = await client.get("/api/products/departamentos")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_rota_de_contagem_nao_colide_com_produto_por_id(client, auth_as, db, make_user):
    admin = await make_user(role=UserRole.ADMIN)
    produtos = await _cenario(db, admin)
    auth_as(admin)
    r = await client.get("/api/products/departamentos")
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"produtos", "anuncios"}
    r = await client.get(f"/api/products/{produtos['mala'].id}")
    assert r.status_code == 200
    assert r.json()["sku"] == "sku-mala"


@pytest.mark.asyncio
async def test_saude_dos_vinculos_e_remover_mortos_por_departamento(client, auth_as, db, make_user):
    """A faixa "Vínculos: N mortos…" conta só o departamento da tela, e o
    "Remover os N mortos" (com o mesmo departamento) apaga só esses N."""
    from datetime import UTC, datetime
    from unittest.mock import patch

    from sqlalchemy import select

    from app.services.vinculo_saude import apagar_mortos_em_lotes

    admin = await make_user(role=UserRole.ADMIN)
    produtos = await _cenario(db, admin)
    integ = await _integracao(db, admin)
    for chave in ("celular", "catalogo", "mala", "sem"):
        db.add(
            ProductLink(
                user_id=admin.id,
                product_id=produtos[chave].id,
                integration_id=integ.id,
                platform=IntegrationPlatform.SHOPEE,
                external_id=f"lk-{chave}",
                external_sku=produtos[chave].sku,
                morto_desde=datetime.now(UTC),
                morto_motivo="anúncio excluído na Shopee",
            )
        )
    await db.commit()
    auth_as(admin)

    async def mortos(dep: str | None = None) -> int:
        r = await client.get("/api/products", params={"departamento": dep} if dep else {})
        assert r.status_code == 200, r.text
        return r.json()["saude"]["mortos"]

    assert await mortos() == 4  # sem o parâmetro, como antes: todos
    assert await mortos("celular") == 2  # Celular + Catálogo
    assert await mortos("mala") == 1
    assert await mortos("eletro") == 0  # a faixa some
    assert await mortos("sem") == 1

    agendados = []

    class _Pool:
        async def enqueue_job(self, *a, **k):
            agendados.append((a, k))

    async def _pool():
        return _Pool()

    with patch("app.routers.products.get_arq_pool", _pool):
        r = await client.post(
            "/api/product-links/remover-mortos", params={"departamento": "eletro"}
        )
        assert r.json() == {"agendado": True, "quantidade": 0}
        r = await client.post(
            "/api/product-links/remover-mortos", params={"departamento": "celular"}
        )
        assert r.json() == {"agendado": True, "quantidade": 2}
        r = await client.post("/api/product-links/remover-mortos", params={"departamento": "x"})
        assert r.status_code == 400
    assert agendados == [(("remover_vinculos_mortos_run", None, True), {"departamento": "celular"})]

    # O job apaga só os do departamento; os de Mala e Sem departamento ficam.
    assert await apagar_mortos_em_lotes(somente_visiveis=True, departamento="celular", pausa=0) == 2
    restantes = set((await db.execute(select(ProductLink.external_id))).scalars().all())
    assert restantes == {"lk-mala", "lk-sem"}
    assert await mortos("celular") == 0
    assert await mortos() == 2


def test_somar_por_departamento_sem_segmentos():
    """Sem nenhuma raiz conhecida, tudo é Sem departamento (nada some)."""
    sid = uuid.uuid4()
    assert dep_svc.somar_por_departamento([(None, 2), (sid, 3)], {}) == {
        "celular": 0,
        "mala": 0,
        "eletro": 0,
        "sem": 5,
    }
    assert dep_svc.somar_por_departamento([(sid, 4)], {sid: "mala"})["mala"] == 4
    assert dep_svc.validar(None) is None
    assert dep_svc.validar("") is None
    assert dep_svc.validar("eletro") == "eletro"
