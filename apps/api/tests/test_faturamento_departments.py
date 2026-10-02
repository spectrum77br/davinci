"""Tipos de Lojas em Faturamento: mesmos vínculos, receita e escopo preservados."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio

from app.models import BlingOrder, PricingAccount, PricingPlatform, Segment, StoreInfo, UserRole

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

    # Equipe comercial organiza as lojas; sales_team continua sendo a trava
    # de acesso. Mesmos nomes em plataformas diferentes não misturam tipos.
    plan = (
        ("legacy_ml", "ml", "kfa", 11, 1, None),
        ("kfa2", "mercadolivre", "kfa2", 22, 1, None),
        ("shopee", "shopee", "kfa", 11, 2, None),
        ("explicit_fk", "mercadolivre", "Outro nome", 22, 2, None),
        ("site", "site", "kfa", 11, 1, ["mala"]),
        ("carrefour", "carrefour", "kfa", 22, 1, ["mala", "catalogo"]),
        ("netshoes", "netshoes", "kfa", 11, 2, ["eletro", "mala"]),
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
    accounts = (
        ("KFA CLASSICO", PricingPlatform.ML, "celular", None),
        ("kfa premium", PricingPlatform.ML, "mala", None),
        ("kfa2 classico", PricingPlatform.ML, "eletro", None),
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
    await db.commit()
    return {"owner": owner, "stores": stores, "roots": roots}


async def _revenue(client, **filters):
    response = await client.get("/api/faturamento", params={**PERIOD, **filters})
    assert response.status_code == 200, response.text
    return response.json()


def _ids(stores, *keys):
    return {str(stores[key].id) for key in keys}


async def _seed_revenue(db, stores):
    plan = (
        ("legacy_ml", "100.00", "6", 3, WHEN),
        ("legacy_ml", "250.00", "15", 2, WHEN),
        ("legacy_ml", "999.00", "12", 1, WHEN),
        ("legacy_ml", "777.00", "83953", 1, WHEN - timedelta(days=90)),
        ("kfa2", "200.00", "83953", 1, WHEN),
        ("shopee", "400.00", "83953", 1, WHEN),
        ("site", "75.25", "83953", 2, WHEN),
        ("carrefour", "50.00", "83953", 1, WHEN),
        ("child_only", "25.00", "83953", 1, WHEN),
        ("untyped", "30.00", "83953", 1, WHEN),
        (None, "60.00", "83953", 2, WHEN),
    )
    for bling_id, (key, amount, status, items, when) in enumerate(plan, start=95001):
        for item_index in range(items):
            db.add(BlingOrder(bling_id=bling_id, numero=str(bling_id), item_index=item_index,
                             item_codigo=f"sku-{bling_id}-{item_index}",
                             loja=stores[key].bling_store_id if key else "orphan-99",
                             total=Decimal(amount), situacao=status, data=when))
    await db.commit()


async def test_revenue_departments_match_stores_without_changing_platform_field(
    client, auth_as, department_roster,
):
    auth_as(department_roster["owner"])
    stores = department_roster["stores"]
    expected = {
        "legacy_ml": ["celular", "mala"],
        "kfa2": ["eletro"],
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


async def test_department_filter_preserves_order_dedup_status_period_and_zero_sale_stores(
    db, client, auth_as, department_roster,
):
    auth_as(department_roster["owner"])
    stores = department_roster["stores"]
    await _seed_revenue(db, stores)
    baseline = await _revenue(client)
    assert baseline["total_pedidos"] == 9
    assert baseline["total_faturamento"] == 1190.25
    orphan = next(row for row in baseline["itens"] if row["store_id"] == "orphan-99")
    assert orphan["departments"] == []
    assert orphan["tipo"] is None
    assert orphan["pedidos"] == 1
    assert orphan["faturamento"] == 60

    filtered = await _revenue(client, department="mala")
    rows = {row["store_id"]: row for row in filtered["itens"]}
    assert set(rows) == _ids(stores, "legacy_ml", "site", "carrefour", "netshoes", "zero")
    assert filtered["total_pedidos"] == 4
    assert filtered["total_faturamento"] == 475.25
    assert rows[str(stores["legacy_ml"].id)]["pedidos"] == 2
    assert rows[str(stores["legacy_ml"].id)]["ticket_medio"] == 175
    for key in ("netshoes", "zero"):
        assert rows[str(stores[key].id)]["faturamento"] == 0
        assert rows[str(stores[key].id)]["pedidos"] == 0
    # Remover o filtro repõe os órfãos e o faturamento que ficou fora do tipo.
    again = await _revenue(client)
    assert again["itens"] == baseline["itens"]
    assert again["total_faturamento"] == baseline["total_faturamento"]


@pytest.mark.parametrize(("department", "keys", "total", "orders"), (
    ("catalogo", ("shopee", "carrefour"), 450, 2),
    ("eletro", ("kfa2", "explicit_fk", "netshoes"), 200, 1),
    ("celular", ("legacy_ml",), 350, 2),
))
async def test_admin_can_filter_each_root_with_financial_totals_of_matching_stores(
    db, client, auth_as, department_roster, department, keys, total, orders,
):
    auth_as(department_roster["owner"])
    stores = department_roster["stores"]
    await _seed_revenue(db, stores)
    body = await _revenue(client, department=department)
    assert {row["store_id"] for row in body["itens"]} == _ids(stores, *keys)
    assert body["total_faturamento"] == total
    assert body["total_pedidos"] == orders


async def test_admin_department_and_commercial_team_filters_intersect(
    db, client, auth_as, department_roster,
):
    auth_as(department_roster["owner"])
    stores = department_roster["stores"]
    await _seed_revenue(db, stores)
    body = await _revenue(client, department="mala", team=2)
    assert {row["store_id"] for row in body["itens"]} == _ids(stores, "netshoes")
    assert body["total_faturamento"] == 0
    assert body["total_pedidos"] == 0
    assert body["teams"] == [1, 2]
    assert body["team"] == 2


async def test_valid_department_without_stores_returns_zero_without_orphan_revenue(
    db, client, auth_as, department_roster,
):
    auth_as(department_roster["owner"])
    await _seed_revenue(db, department_roster["stores"])
    db.add(Segment(name="Novo tipo", slug="novo-tipo"))
    await db.commit()
    body = await _revenue(client, department="novo-tipo")
    assert body["itens"] == []
    assert body["total_faturamento"] == 0
    assert body["total_pedidos"] == 0


@pytest.mark.parametrize("department", ("unknown-root", "mala-usada"))
async def test_admin_department_filter_rejects_unknown_roots_and_child_segments(
    client, auth_as, department_roster, department,
):
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
        body = await _revenue(client, team=1)
        assert {row["store_id"] for row in body["itens"]} == _ids(
            stores, "legacy_ml", "site", "zero",
        )
        assert body["total_faturamento"] == 425.25
    else:
        assert "orphan-99" in reported
        assert body["total_faturamento"] == 1190.25
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
