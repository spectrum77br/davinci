"""Tipos de Lojas em Faturamento: mesmos vínculos, receita e escopo preservados.

17/11/2026 (Novos procedimentos 7): o filtro "Tipo" (só admin) é o
departamento do PRODUTO vendido — Celular, Mala, Eletro ou Sem categoria. Cada
pedido se divide entre os itens (itemvalor × quantidade; soma 0 → partes
iguais) e cada loja mostra só o que vendeu daquilo; os quatro somam o total
sem filtro. A etiqueta de tipo da loja (contas de preço) continua só como
etiqueta. Os testes com `db`/`client` precisam do Postgres do conftest; os
PUROS (departamento_do_item) rodam sem banco.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
import pytest_asyncio

from app.models import (
    BlingOrder,
    PricingAccount,
    PricingPlatform,
    Product,
    Segment,
    StoreInfo,
    UserRole,
)
from app.services import departamento as dep_svc

WHEN = datetime(2026, 10, 2, 12, tzinfo=UTC)
PERIOD = {
    "start": (WHEN - timedelta(days=1)).isoformat(),
    "end": (WHEN + timedelta(days=1)).isoformat(),
}
REVENUE_PERMISSION = {"faturamento": {"view": True}}


@pytest_asyncio.fixture
async def department_roster(db, make_user):
    owner = await make_user(role=UserRole.ADMIN)
    roots = {slug: Segment(name=slug.title(), slug=slug) for slug in (
        "celular", "mala", "eletro", "catalogo",
    )}
    db.add_all(roots.values())
    await db.flush()
    child = Segment(name="Mala usada", slug="mala-usada", parent_id=roots["mala"].id)
    db.add(child)
    await db.flush()

    # sales_team é a trava de acesso e, desde 30/10/2026, o filtro "Equipe";
    # a comercial (1/2) fica de ruído, não decide nada no Faturamento.
    # Mesmos nomes em plataformas diferentes não misturam tipos.
    plan = (
        ("legacy_ml", "ml", "kfa", 11, 1, None),
        ("kfa2", "mercadolivre", "kfa2", 22, 1, None),
        ("shopee", "shopee", "kfa", 11, 2, None),
        ("explicit_fk", "mercadolivre", "Outro nome", 22, 2, None),
        ("site", "site", "kfa", 11, 1, ["mala"]),
        ("carrefour", "carrefour", "kfa", 22, 1, None),
        ("netshoes", "netshoes", "kfa", 11, 2, None),
        ("child_only", "amazon", "Só subtipo", 22, 2, None),
        ("zero", "amazon", "Sem vendas", 11, 1, None),
        ("untyped", "magalu", "Sem tipo", 22, 2, None),
    )
    stores = {}
    for index, (key, platform, name, sales_team, team, manual) in enumerate(plan, start=1001):
        store = StoreInfo(user_id=owner.id, platform=platform, account_name=name,
                          bling_store_id=str(index), sales_team=sales_team,
                          commercial_team=team, manual_departments=manual)
        db.add(store)
        stores[key] = store
    await db.flush()

    # FK explícita prevalece sobre o nome: esta conta "kfa" NÃO pertence à
    # loja legacy_ml. Contas legadas dependem de plataforma + palavra inteira.
    # kfa2 é Celular+Eletro (como 61 das 91 lojas em out/2026): no filtro ela
    # aparece em cada um só com a sua parte.
    accounts = (
        ("KFA CLASSICO", PricingPlatform.ML, "celular", None),
        ("kfa premium", PricingPlatform.ML, "mala", None),
        ("kfa2 classico", PricingPlatform.ML, "eletro", None),
        ("kfa2 premium", PricingPlatform.ML, "celular", None),
        ("kfa", PricingPlatform.SHOPEE, "catalogo", None),
        ("kfa", PricingPlatform.ML, "eletro", "explicit_fk"),
        ("Sem vendas clássico", PricingPlatform.AMAZON, "mala", "zero"),
        ("Sem vendas premium", PricingPlatform.AMAZON, "mala", "zero"),
    )
    for name, platform, slug, store_key in accounts:
        db.add(PricingAccount(user_id=owner.id, name=name, platform=platform,
                             segment_id=roots[slug].id,
                             store_info_id=stores[store_key].id if store_key else None))
    db.add(PricingAccount(user_id=owner.id, name="Só subtipo", platform=PricingPlatform.AMAZON,
                         segment_id=child.id, store_info_id=stores["child_only"].id))
    # Carrefour e Netshoes têm tabela de preços desde a 0379 (07/10/2026): o
    # tipo vem da conta ligada à loja; o manual ficou só para o Site.
    for store_key, platform, slugs in (
        ("carrefour", PricingPlatform.CARREFOUR, ("mala", "catalogo")),
        ("netshoes", PricingPlatform.NETSHOES, ("eletro", "mala")),
    ):
        for slug in slugs:
            db.add(
                PricingAccount(
                    user_id=owner.id,
                    name=f"kfa {slug}",
                    platform=platform,
                    segment_id=roots[slug].id,
                    store_info_id=stores[store_key].id,
                )
            )
    # Os produtos dos itens vendidos: o departamento sai da RAIZ do segmento
    # (b900.20 está numa folha da Mala); uaf002m1.110 é o SKU novo de Eletro
    # que vem sem categoria do Bling no pedido.
    for sku, segment in (
        ("dg900.sp", roots["celular"]),
        ("b900.20", child),
        ("uaf002m1.110", roots["eletro"]),
    ):
        db.add(Product(user_id=owner.id, sku=sku, name=sku, segment_id=segment.id))
    await db.commit()
    return {"owner": owner, "stores": stores, "roots": roots}


async def _revenue(client, **filters):
    response = await client.get("/api/faturamento", params={**PERIOD, **filters})
    assert response.status_code == 200, response.text
    return response.json()


async def _rows(client, **filters):
    """{store_id: linha} da resposta."""
    return {row["store_id"]: row for row in (await _revenue(client, **filters))["itens"]}


def _ids(stores, *keys):
    return {str(stores[key].id) for key in keys}


# Itens: (SKU, itemvalor, quantidade, categoria do item no pedido).
_SEED = (
    # Celular 60 + Mala 20×2 = 40: o total 100 divide 60/40.
    ("legacy_ml", "100.00", "6", WHEN,
     (("dg900.sp", "60", 1, "Celular"), ("b900.20", "20", 2, "Mala"))),
    # Composto sem produto nem categoria: a família do SKU (dg) → Celular.
    ("legacy_ml", "250.00", "15", WHEN, (("dg090.pi+a004.pi", "125", 2, None),)),
    ("legacy_ml", "999.00", "12", WHEN, (("b900.20", "999", 1, "Mala"),)),
    ("legacy_ml", "777.00", "83953", WHEN - timedelta(days=90),
     (("b900.20", "777", 1, "Mala"),)),
    # Valores zerados: partes iguais — Eletro 100 e Celular 100.
    ("kfa2", "200.00", "83953", WHEN,
     (("uaf002m1.110", "0", 1, None), ("dg900.sp", "0", 1, "Celular"))),
    ("shopee", "400.00", "83953", WHEN, (("b900.20", "400", 1, "Mala Kit"),)),
    # b901.24 não tem produto: a categoria do item (Mala Kit) decide.
    ("site", "75.25", "83953", WHEN,
     (("b900.20", "30", 1, "Mala"), ("b901.24", "45.25", 1, "Mala Kit"))),
    # O segmento do produto manda sobre a categoria do pedido; SKU casa sem
    # caixa nem espaço nas pontas.
    ("carrefour", "50.00", "83953", WHEN, ((" DG900.SP", "50", 1, "Mala"),)),
    ("child_only", "25.00", "83953", WHEN, (("a019.cd", "25", 1, "Acessórios Celular"),)),
    # Produto com segmento Eletro e item sem categoria do Bling → Eletro.
    ("untyped", "30.00", "83953", WHEN, (("uaf002m1.110", "30", 1, None),)),
    # Órfão (loja sem cadastro): Mala 10, Celular 20, Sem categoria 30.
    (None, "60.00", "83953", WHEN,
     (("b900.20", "10", 1, "Mala"), ("dg900.sp", "20", 1, "Celular"),
      ("zz-brinde", "30", 1, None))),
)


async def _seed_revenue(db, stores):
    for bling_id, (key, amount, status, when, items) in enumerate(_SEED, start=95001):
        for item_index, (sku, valor, quantidade, categoria) in enumerate(items):
            db.add(BlingOrder(bling_id=bling_id, numero=str(bling_id), item_index=item_index,
                             item_codigo=sku, itemvalor=Decimal(valor),
                             item_quantidade=quantidade, categoria_nome=categoria,
                             loja=stores[key].bling_store_id if key else "orphan-99",
                             total=Decimal(amount), situacao=status, data=when))
    await db.commit()


# O que cada loja vendeu de cada departamento no seed: {loja: (pedidos, R$)}.
_POR_DEPARTAMENTO = {
    "celular": {"legacy_ml": (2, 310), "kfa2": (1, 100), "carrefour": (1, 50),
                "child_only": (1, 25), "orphan-99": (1, 20)},
    "mala": {"legacy_ml": (1, 40), "shopee": (1, 400), "site": (1, 75.25),
             "orphan-99": (1, 10)},
    "eletro": {"kfa2": (1, 100), "untyped": (1, 30)},
    "sem": {"orphan-99": (1, 30)},
}


def _row_key(stores, store_id):
    return next((key for key, store in stores.items() if str(store.id) == store_id), store_id)


async def test_revenue_departments_match_stores_without_changing_platform_field(
    client, auth_as, department_roster,
):
    auth_as(department_roster["owner"])
    stores = department_roster["stores"]
    expected = {
        "legacy_ml": ["celular", "mala"],
        "kfa2": ["celular", "eletro"],
        "shopee": ["catalogo"],
        "explicit_fk": ["eletro"],
        "site": ["mala"],
        "carrefour": ["catalogo", "mala"],
        "netshoes": ["eletro", "mala"],
        "child_only": [],
        "zero": ["mala"],
        "untyped": [],
    }
    store_response = await client.get("/api/pricing/store-info")
    assert store_response.status_code == 200, store_response.text
    registered = {row["id"]: row for row in store_response.json()}
    body = await _revenue(client)
    reported = {row["store_id"]: row for row in body["itens"]}
    assert set(reported) == _ids(stores, *expected)
    for key, departments in expected.items():
        store_id = str(stores[key].id)
        assert reported[store_id]["departments"] == departments
        assert reported[store_id]["departments"] == registered[store_id]["departments"]
        assert reported[store_id]["tipo"] == stores[key].platform
        assert reported[store_id]["pedidos"] == 0
        assert reported[store_id]["faturamento"] == 0
        assert reported[store_id]["ticket_medio"] == 0
    assert body["total_pedidos"] == 0
    assert body["total_faturamento"] == 0


async def test_department_filter_shows_only_what_each_store_sold_of_it(
    db, client, auth_as, department_roster,
):
    auth_as(department_roster["owner"])
    stores = department_roster["stores"]
    await _seed_revenue(db, stores)
    baseline = await _revenue(client)
    # Sem filtro, nada muda: dedup por pedido, situações e período de sempre.
    assert baseline["total_pedidos"] == 9
    assert baseline["total_faturamento"] == 1190.25
    orphan = next(row for row in baseline["itens"] if row["store_id"] == "orphan-99")
    assert orphan["departments"] == []
    assert orphan["tipo"] is None
    assert orphan["pedidos"] == 1
    assert orphan["faturamento"] == 60

    soma = 0.0
    for department, expected in _POR_DEPARTAMENTO.items():
        body = await _revenue(client, department=department)
        rows = {_row_key(stores, row["store_id"]): row for row in body["itens"]}
        # Loja que não vendeu o departamento não aparece (netshoes, zero,
        # explicit_fk nunca aparecem: não venderam nada).
        assert set(rows) == set(expected), department
        for key, (pedidos, faturamento) in expected.items():
            assert rows[key]["pedidos"] == pedidos, (department, key)
            assert rows[key]["faturamento"] == faturamento, (department, key)
        assert body["total_pedidos"] == sum(p for p, _ in expected.values())
        assert body["total_faturamento"] == pytest.approx(sum(v for _, v in expected.values()))
        soma += body["total_faturamento"]
    # Os quatro departamentos somam o total sem filtro.
    assert soma == pytest.approx(baseline["total_faturamento"])

    # Remover o filtro repõe tudo como era.
    again = await _revenue(client)
    assert again["itens"] == baseline["itens"]
    assert again["total_faturamento"] == baseline["total_faturamento"]


async def test_order_with_items_of_two_departments_is_split_by_item_value(
    db, client, auth_as, department_roster,
):
    auth_as(department_roster["owner"])
    stores = department_roster["stores"]
    await _seed_revenue(db, stores)
    legacy = str(stores["legacy_ml"].id)
    celular = await _rows(client, department="celular")
    mala = await _rows(client, department="mala")
    sem_filtro = await _rows(client)
    # Pedido de 100 com Celular 60 e Mala 20 × 2: 60 / 40 (o outro pedido, de
    # 250, é só Celular). O pedido conta nos dois departamentos.
    assert celular[legacy]["faturamento"] == 310
    assert celular[legacy]["pedidos"] == 2
    assert celular[legacy]["ticket_medio"] == 155
    assert mala[legacy]["faturamento"] == 40
    assert mala[legacy]["pedidos"] == 1
    assert celular[legacy]["faturamento"] + mala[legacy]["faturamento"] == (
        sem_filtro[legacy]["faturamento"]
    )
    # Órfão de 60: Mala 10, Celular 20, Sem categoria 30 (pelo valor dos itens).
    sem = await _rows(client, department="sem")
    assert (mala["orphan-99"]["faturamento"], celular["orphan-99"]["faturamento"],
            sem["orphan-99"]["faturamento"]) == (10, 20, 30)


async def test_celular_eletro_store_shows_only_its_part_in_each(
    db, client, auth_as, department_roster,
):
    auth_as(department_roster["owner"])
    stores = department_roster["stores"]
    await _seed_revenue(db, stores)
    kfa2 = str(stores["kfa2"].id)
    sem_filtro = await _rows(client)
    assert sem_filtro[kfa2]["departments"] == ["celular", "eletro"]
    assert sem_filtro[kfa2]["faturamento"] == 200
    for department in ("celular", "eletro"):
        rows = await _rows(client, department=department)
        # Itens de valor zerado: o pedido de 200 vai metade para cada um, e a
        # loja segue com a etiqueta dos dois tipos.
        assert rows[kfa2]["faturamento"] == 100, department
        assert rows[kfa2]["pedidos"] == 1, department
        assert rows[kfa2]["departments"] == ["celular", "eletro"]
    rows = set(await _rows(client, department="mala"))
    assert kfa2 not in rows


async def test_item_department_comes_from_the_product_not_only_the_order_category(
    db, client, auth_as, department_roster,
):
    auth_as(department_roster["owner"])
    stores = department_roster["stores"]
    await _seed_revenue(db, stores)
    eletro = await _rows(client, department="eletro")
    celular = await _rows(client, department="celular")
    mala = set(await _rows(client, department="mala"))
    # uaf002m1.110 vem sem categoria do Bling, mas o produto é do segmento Eletro.
    assert eletro[str(stores["untyped"].id)]["faturamento"] == 30
    # O composto dg090.pi+a004.pi (sem produto, sem categoria) é Celular.
    assert celular[str(stores["legacy_ml"].id)]["faturamento"] == 310
    # " DG900.SP" com categoria "Mala" no pedido: o segmento do produto manda.
    assert celular[str(stores["carrefour"].id)]["faturamento"] == 50
    assert str(stores["carrefour"].id) not in mala


async def test_admin_department_and_sales_team_filters_intersect(
    db, client, auth_as, department_roster,
):
    auth_as(department_roster["owner"])
    stores = department_roster["stores"]
    await _seed_revenue(db, stores)
    body = await _revenue(client, department="celular", team=22)
    # Com equipe, o órfão fica de fora (não tem equipe).
    assert {row["store_id"] for row in body["itens"]} == _ids(
        stores, "kfa2", "carrefour", "child_only"
    )
    assert body["total_faturamento"] == 175
    assert body["total_pedidos"] == 3
    assert body["teams"] == [11, 22]
    # Equipe sem nome cadastrado aparece como "Equipe N".
    assert body["equipes"] == [
        {"numero": 11, "nome": "Equipe 11"},
        {"numero": 22, "nome": "Equipe 22"},
    ]
    assert body["team"] == 22
    body = await _revenue(client, department="mala", team=11)
    assert {row["store_id"] for row in body["itens"]} == _ids(
        stores, "legacy_ml", "shopee", "site"
    )
    assert body["total_faturamento"] == 515.25
    assert body["total_pedidos"] == 3
    # A comercial (1/2) não é mais o filtro: pedir por ela é equipe que não há.
    response = await client.get("/api/faturamento", params={**PERIOD, "team": 2})
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "team_not_allowed"


async def test_department_without_sales_in_period_returns_zero(
    db, client, auth_as, department_roster,
):
    auth_as(department_roster["owner"])
    await _seed_revenue(db, department_roster["stores"])
    body = await _revenue(
        client, department="eletro",
        start=(WHEN - timedelta(days=200)).isoformat(),
        end=(WHEN - timedelta(days=150)).isoformat(),
    )
    assert body["itens"] == []
    assert body["total_faturamento"] == 0
    assert body["total_pedidos"] == 0


@pytest.mark.parametrize(
    "department", ("shein", "catalogo", "unknown-root", "mala-usada", "Celular", ""),
)
async def test_admin_department_filter_accepts_only_product_departments(
    client, auth_as, department_roster, department,
):
    # 17/11/2026: só celular, mala, eletro e sem; Shein, Catálogo e outra raiz
    # de segmento não são departamento de produto.
    auth_as(department_roster["owner"])
    response = await client.get("/api/faturamento", params={**PERIOD, "department": department})
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "invalid_department"


@pytest.mark.parametrize("teams", ([11], None))
async def test_revenue_viewer_sees_types_without_store_permission_but_cannot_filter_them(
    db, client, make_user, auth_as, department_roster, teams,
):
    stores = department_roster["stores"]
    await _seed_revenue(db, stores)
    viewer = await make_user(permissions=REVENUE_PERMISSION)
    viewer.sales_teams = teams
    viewer.commercial_team = 1
    await db.commit()
    auth_as(viewer)
    assert (await client.get("/api/pricing/store-info")).status_code == 403
    body = await _revenue(client)
    reported = {row["store_id"]: row for row in body["itens"]}
    assert reported[str(stores["site"].id)]["departments"] == ["mala"]
    assert reported[str(stores["legacy_ml"].id)]["departments"] == ["celular", "mala"]
    if teams:
        assert set(reported) == _ids(stores, "legacy_ml", "shopee", "site", "netshoes", "zero")
        assert body["total_faturamento"] == 825.25
        assert body["total_pedidos"] == 4
        # Uma equipe só: o filtro não tem o que escolher (a tela esconde).
        assert body["teams"] == [11]
        assert (await _revenue(client, team=11))["itens"] == body["itens"]
        response = await client.get("/api/faturamento", params={**PERIOD, "team": 22})
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["code"] == "team_not_allowed"
    else:
        assert "orphan-99" in reported
        assert body["total_faturamento"] == 1190.25
        # Sem equipe vê tudo e pode afunilar; com filtro o órfão fica de fora.
        assert body["teams"] == [11, 22]
        body = await _revenue(client, team=22)
        assert {row["store_id"] for row in body["itens"]} == _ids(
            stores, "kfa2", "explicit_fk", "carrefour", "child_only", "untyped"
        )
        assert body["total_faturamento"] == 305
        assert body["total_pedidos"] == 4
    for department in ("mala", "unknown-root", ""):
        response = await client.get("/api/faturamento", params={
            **PERIOD, "department": department,
        })
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["code"] == "department_not_allowed"


async def test_store_permission_alone_does_not_grant_access_to_revenue(
    client, make_user, auth_as,
):
    auth_as(await make_user(permissions={"lojas_info": {"view": True}}))
    response = await client.get("/api/faturamento", params=PERIOD)
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["resource"] == "faturamento"


# ── PUROS: o departamento do item vendido (sem banco) ─────────────────────────

def _idx():
    """Uma raiz de cada departamento e uma fora deles ("outra"), no formato do
    indice_departamentos: {segment_id: departamento}."""
    ids = {slug: UUID(int=n) for n, slug in enumerate(("celular", "mala", "eletro", "outra"), 1)}
    idx = {ids[slug]: dep for slug, dep in dep_svc.RAIZ_PARA_DEPARTAMENTO.items() if slug in ids}
    return ids, idx


@pytest.mark.parametrize(("sku", "categoria", "esperado"), (
    # Verificado em produção (17/11/2026): SKUs que chegam sem categoria do Bling.
    ("uaf002m1.110", None, "eletro"),
    ("uaf002m1.220", "", "eletro"),
    ("usl001m1.110", None, "eletro"),
    ("dg090.pi+a004.pi", None, "celular"),
    ("dg101.pi", None, "celular"),
    # Kits, compostos e acessórios de celular → Celular; Mala Kit → Mala.
    ("a019.cd", "Acessórios Celular", "celular"),
    ("personalizado", "Celular Kit", "celular"),
    ("b035.28", "Mala Kit", "mala"),
    ("x1", "Eletro Usado", "eletro"),
    # Nada decide → Sem categoria.
    ("zz-brinde", None, "sem"),
    (None, None, "sem"),
    ("", "Insumos", "sem"),
))
def test_item_sem_produto_vai_pela_categoria_do_pedido_e_pela_familia(sku, categoria, esperado):
    _, idx = _idx()
    assert dep_svc.departamento_do_item(sku, categoria, idx) == esperado


def test_item_com_produto_vai_pelo_segmento_raiz_antes_da_categoria():
    ids, idx = _idx()
    produto = dep_svc.ProdutoDoItem(segment_id=ids["celular"])
    # O pedido diz "Mala", a família diria Mala: o segmento do produto manda.
    assert dep_svc.departamento_do_item("b900.20", "Mala", idx, produto) == "celular"
    produto = dep_svc.ProdutoDoItem(segment_id=ids["eletro"])
    assert dep_svc.departamento_do_item("uaf002m1.110", None, idx, produto) == "eletro"


def test_item_com_produto_sem_segmento_conhecido_usa_as_categorias_do_bling_na_ordem():
    ids, idx = _idx()
    # Raiz fora dos departamentos: cai no reserva — a categoria do produto
    # vem antes da do cadastro, que vem antes da do item no pedido.
    produto = dep_svc.ProdutoDoItem(ids["outra"], "Eletro", "Mala")
    assert dep_svc.departamento_do_item("dg001", "Celular", idx, produto) == "eletro"
    produto = dep_svc.ProdutoDoItem(None, None, "Mala")
    assert dep_svc.departamento_do_item("dg001", "Celular", idx, produto) == "mala"
    produto = dep_svc.ProdutoDoItem(None, "Insumos", None)
    assert dep_svc.departamento_do_item("dg001", "Eletro Kit", idx, produto) == "eletro"
    # Sem categoria nenhuma: a família do SKU; nem ela → "sem".
    produto = dep_svc.ProdutoDoItem(None)
    assert dep_svc.departamento_do_item("dg001", None, idx, produto) == "celular"
    assert dep_svc.departamento_do_item("zz", None, idx, produto) == "sem"


def test_departamentos_do_filtro_sao_os_do_produto():
    # O filtro aceita exatamente estes (shein/catalogo → 400 no endpoint).
    assert dep_svc.DEPARTAMENTOS == ("celular", "mala", "eletro", "sem")
