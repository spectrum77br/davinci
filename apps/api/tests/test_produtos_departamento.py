"""Produtos e Anúncios por departamento (Organização do DaVinci, 13/11/2026, item 2).

Celular (com o Catálogo), Mala, Eletro e Sem departamento — pela raiz do
segmento do produto; sem segmento de raiz conhecida, pelo reserva (categoria
do Bling, senão a família do SKU, 15/11/2026). O anúncio vale pelo produto
vinculado e, sem produto, cai em Sem departamento. O conftest usa create_all
(não roda o seed das raízes da 0023/0024), então cada teste cria as raízes que
usa.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Company,
    Integration,
    IntegrationPlatform,
    Listing,
    ListingStatus,
    Marketplace,
    Product,
    ProductCategory,
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


def test_somar_e_validar():
    """Departamento desconhecido (ou nulo) conta como Sem departamento: nada some."""
    assert dep_svc.somar([("mala", 2), ("celular", 3), (None, 1), ("xyz", 4), ("mala", 1)]) == {
        "celular": 3,
        "mala": 3,
        "eletro": 0,
        "sem": 5,
    }
    assert dep_svc.validar(None) is None
    assert dep_svc.validar("") is None
    assert dep_svc.validar("eletro") == "eletro"


# ── Reserva (15/11/2026): categoria do Bling, senão a família do SKU ─────────

# (sku, products.categoria_nome, product_categories.name, segmento, esperado).
# Os de cima são produtos REAIS sem segmento da produção (dry-run de 15/11);
# os de baixo, os cantos da regra (ordem, acento, caixa, espaço, kit).
CASOS_RESERVA: list[tuple[str, str | None, str | None, str | None, str]] = [
    ("dg050.ci+a003.ci", None, "Celular Kit", None, "celular"),
    ("dg1007.pi+a003.pi+a019.pi+a020.pi", "Celular Kit", "Celular Kit", None, "celular"),
    ("x064.ci", None, "Celular", None, "celular"),  # só a categoria decide
    ("z0188", None, "Celular Usado", None, "celular"),
    ("z0382", None, "Celular", None, "celular"),
    ("i020.us", None, "Celular Usado", None, "celular"),
    ("a014.us", None, "Celular Usado", None, "celular"),
    ("caixa 24", None, "Acessórios Celular", None, "celular"),
    ("a073.us", None, "Acessórios Celular", None, "celular"),
    ("a006.sp", None, None, None, "celular"),  # o override de mala é SKU exato
    ("ha001.110", None, "Eletro", None, "eletro"),
    ("usl001m1.110", None, None, None, "eletro"),  # família u
    ("kit4personalizado", None, "Mala Kit", None, "mala"),
    ("b000.kit15", None, "Mala Usada", None, "mala"),
    ("z0367.mala", None, "Mala", None, "mala"),
    ("z0093.mala", None, None, None, "mala"),  # z….mala sem categoria
    ("z0207.mala.us", None, None, None, "mala"),  # override do sku_tags
    ("a006", None, "Mala", None, "mala"),
    ("a075", None, None, None, "mala"),  # override, sem categoria (seria a0NN)
    ("bp001+a019+a020", None, "Mala Kit", None, "mala"),
    ("bp003+a019+a020", None, None, None, "mala"),  # família bp
    ("b069.14.20.24+a075+bp003+a076", "Mala Kit", None, None, "mala"),
    ("s001", None, "Insumos", None, "sem"),
    ("a1003", None, None, None, "sem"),
    ("z0197", None, None, None, "sem"),  # z sem .mala/.eletro: a regra não decide
    ("t004.sa+a004.sa", None, None, None, "sem"),
    ("fake.sp", None, None, None, "sem"),
    ("teste-geral-1", None, "Geral", None, "sem"),
    # Ordem: segmento de raiz conhecida > categoria_nome > cadastro > família.
    ("dg053.sp", "Celular", "Celular", "mala", "mala"),
    ("dg054.sp", None, None, "celular-raiz", "celular"),
    ("b021.24", None, "Mala", "outra", "mala"),  # raiz desconhecida → reserva
    ("dg055.sp", None, None, "outra", "celular"),
    ("x-ordem-1", "Eletro Kit", "Mala", None, "eletro"),
    ("x-ordem-2", "Embalagem", "Mala", None, "mala"),  # categoria_nome não decide
    ("dg056.sp", None, "Mala Kit", None, "mala"),  # categoria > família
    ("u020", "", "Insumos", None, "eletro"),
    # Acento, caixa e espaço (o Python tira acento por NFKD; o SQL, por tabela).
    ("x-cat-1", "  ACESSÓRIOS celular", None, None, "celular"),
    ("x-cat-2", "Elétro Usado", None, None, "eletro"),
    ("x-cat-3", "\tmala kit", None, None, "mala"),
    ("x-cat-4", "Malas de bordo", None, None, "mala"),
    ("x-cat-5", "Kit Celular", None, None, "sem"),  # tem que COMEÇAR com
    (" DG101.PI ", None, None, None, "celular"),
    ("U015", None, None, None, "eletro"),
    ("Z0099.MALA", None, None, None, "mala"),
    ("I3.sa", None, None, None, "celular"),
    ("A015", None, None, None, "mala"),
    ("dg001.us+z0005.mala", None, None, None, "mala"),  # z….mala em qualquer peça
    ("z0010.eletro", None, None, None, "eletro"),
    ("zmala", None, None, None, "sem"),
    ("a0x1", None, None, None, "sem"),
]


@pytest.mark.asyncio
async def test_reserva_sql_igual_ao_python(db, make_user):
    """O CASE em SQL (que filtra e conta) e a função em Python dão o mesmo
    departamento — e o esperado — em SKUs e categorias reais e nos cantos."""
    user = await make_user()
    seg = await _segmentos(db)
    categorias: dict[str, int] = {}
    for nome in sorted({c[2] for c in CASOS_RESERVA if c[2]}):
        categorias[nome] = 9000 + len(categorias)
        db.add(ProductCategory(bling_category_id=categorias[nome], name=nome))
    await db.flush()
    for i, (sku, categoria_nome, cadastro, segmento, _) in enumerate(CASOS_RESERVA):
        db.add(
            Product(
                user_id=user.id,
                sku=sku,
                name=f"Produto {i}",
                stock=0,
                min_stock=0,
                categoria_nome=categoria_nome,
                # O id do Bling vem como texto (às vezes com espaço).
                category=f" {categorias[cadastro]} " if cadastro else None,
                segment_id=seg[segmento].id if segmento else None,
            )
        )
    await db.commit()

    idx = await dep_svc.indice_departamentos(db)
    linhas = await db.execute(select(Product.sku, dep_svc.expressao_departamento(idx)))
    pelo_sql = dict(linhas.all())
    assert len(pelo_sql) == len(CASOS_RESERVA)
    for sku, categoria_nome, cadastro, segmento, esperado in CASOS_RESERVA:
        segment_id = seg[segmento].id if segmento else None
        pelo_python = dep_svc.departamento_do_produto(
            segment_id, idx, sku, categoria_nome, cadastro
        )
        assert (sku, pelo_sql[sku], pelo_python) == (sku, esperado, esperado)


@pytest.mark.asyncio
async def test_reserva_nas_telas(client, auth_as, db, make_user):
    """Produto sem segmento entra no departamento da categoria / família em
    todas as telas: lista de Produtos, contagem, Anúncios (pelo produto
    vinculado) e o "Remover os N mortos". O segmento de raiz conhecida
    continua mandando."""
    from datetime import UTC, datetime
    from unittest.mock import patch

    admin = await make_user(role=UserRole.ADMIN)
    seg = await _segmentos(db)
    integ = await _integracao(db, admin)
    db.add(ProductCategory(bling_category_id=777, name="Mala Kit"))
    await db.flush()
    produtos: dict[str, Product] = {}
    for sku, categoria_nome, category, segmento in (
        ("dg101.pi", None, None, None),  # família → Celular
        ("b021.24", None, "777", None),  # cadastro → Mala
        ("kit-x", "Eletro", None, None),  # categoria_nome → Eletro
        ("dg053.sp", "Celular", None, "mala"),  # o segmento manda → Mala
        ("b050", None, None, "outra"),  # raiz desconhecida → família → Mala
        ("s001", "Insumos", None, None),  # nada decide → Sem departamento
    ):
        p = Product(
            user_id=admin.id,
            sku=sku,
            name=f"Produto {sku}",
            stock=0,
            min_stock=0,
            categoria_nome=categoria_nome,
            category=category,
            segment_id=seg[segmento].id if segmento else None,
        )
        db.add(p)
        await db.flush()
        produtos[sku] = p
        db.add(
            Listing(
                user_id=admin.id,
                integration_id=integ.id,
                platform=IntegrationPlatform.SHOPEE,
                external_id=f"ext-{sku}",
                sku=sku,
                title=f"Anúncio {sku}",
                status=ListingStatus.ACTIVE,
                product_id=p.id,
            )
        )
        db.add(
            ProductLink(
                user_id=admin.id,
                product_id=p.id,
                integration_id=integ.id,
                platform=IntegrationPlatform.SHOPEE,
                external_id=f"lk-{sku}",
                external_sku=sku,
                morto_desde=datetime.now(UTC),
                morto_motivo="anúncio excluído na Shopee",
            )
        )
    db.add(
        Listing(
            user_id=admin.id,
            integration_id=integ.id,
            platform=IntegrationPlatform.SHOPEE,
            external_id="ext-solto",
            sku="dg999.pi",
            title="Anúncio sem produto",
            status=ListingStatus.PAUSED,
            product_id=None,
        )
    )
    await db.commit()
    auth_as(admin)

    async def skus(dep: str) -> set[str]:
        r = await client.get(f"/api/products?departamento={dep}&page_size=200")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == len(body["items"])
        return {i["sku"] for i in body["items"]}

    assert await skus("celular") == {"dg101.pi"}
    assert await skus("mala") == {"b021.24", "dg053.sp", "b050"}
    assert await skus("eletro") == {"kit-x"}
    assert await skus("sem") == {"s001"}

    r = await client.get("/api/products/departamentos")
    assert r.status_code == 200, r.text
    assert r.json() == {
        "produtos": {"celular": 1, "mala": 3, "eletro": 1, "sem": 1},
        # O anúncio sem produto fica em Sem departamento, mesmo com SKU de celular.
        "anuncios": {"celular": 1, "mala": 3, "eletro": 1, "sem": 2},
    }

    async def titulos(dep: str) -> set[str]:
        r = await client.get(f"/api/listings?departamento={dep}&page_size=200")
        assert r.status_code == 200, r.text
        return {i["title"] for i in r.json()["items"]}

    assert await titulos("eletro") == {"Anúncio kit-x"}
    assert await titulos("sem") == {"Anúncio s001", "Anúncio sem produto"}

    # A faixa de vínculos e o "Remover os N mortos" ficam no mesmo departamento.
    r = await client.get("/api/products", params={"departamento": "mala"})
    assert r.json()["saude"]["mortos"] == 3

    class _Pool:
        async def enqueue_job(self, *a, **k):
            pass

    async def _pool():
        return _Pool()

    with patch("app.routers.products.get_arq_pool", _pool):
        r = await client.post("/api/product-links/remover-mortos", params={"departamento": "mala"})
    assert r.json() == {"agendado": True, "quantidade": 3}

    # Classificar num segmento de Celular tira o produto da Mala do reserva.
    produtos["b021.24"].segment_id = seg["celular"].id
    await db.commit()
    assert await skus("mala") == {"dg053.sp", "b050"}
    assert await skus("celular") == {"dg101.pi", "b021.24"}
