"""Carrefour e Netshoes: tipos de loja geram contas sem inventar tarifas."""

import asyncio
from decimal import Decimal
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.models import Integration, PricingAccount, Segment, StoreInfo

PLATFORMS = ("carrefour", "netshoes")
PERMISSIONS = {
    "lojas_info": {"view": True, "edit": True, "delete": True},
    "tabela_precos_contas": {"view": True, "edit": True},
}
MONEY_FIELDS = ("commission",) + tuple(
    f"{kind}{slot}" for slot in range(1, 6) for kind in ("margin", "shipping")
)


@pytest_asyncio.fixture
async def editor(db, make_user, auth_as):
    user = await make_user(permissions=PERMISSIONS)
    db.add_all(Segment(name=slug.title(), slug=slug) for slug in ("celular", "mala", "eletro"))
    await db.commit()
    auth_as(user)
    return user


async def _store(client, platform, **extra):
    response = await client.post("/api/pricing/store-info", json={
        "platform": platform, "account_name": "Poofy", **extra,
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _select(client, store_id, department):
    response = await client.post(f"/api/pricing/store-info/{store_id}/department", json={
        "department": department,
    })
    assert response.status_code == 200, response.text
    return response.json()


async def _accounts(client, platform, department=None):
    params = {"platform": platform}
    if department:
        params["department"] = department
    response = await client.get("/api/pricing/accounts", params=params)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("platform", PLATFORMS)
async def test_selected_types_appear_in_accounts_without_fees_or_integrations(
    client, db, editor, platform,
):
    store_id = await _store(client, platform, account_name="VR ")
    # A inscrição da loja sozinha continua sem tabela de preço.
    assert await _accounts(client, platform) == []
    created = [await _select(client, store_id, slug) for slug in ("celular", "eletro")]
    assert {row["department"] for row in created} == {"celular", "eletro"}
    assert {row["id"] for row in await _accounts(client, platform)} == {
        row["id"] for row in created
    }
    assert len(await _accounts(client, platform, "celular")) == 1
    assert await _accounts(client, platform, "mala") == []
    for row in created:
        assert row["name"] == "VR"
        assert row["platform"] == platform
        assert row["store_info_id"] == store_id
        assert row["canal"] == "kit"
        assert row["integration_id"] is None
        assert all(row[field] is None for field in MONEY_FIELDS)
        stored = await db.get(PricingAccount, UUID(row["id"]))
        assert stored.user_id == editor.id
    assert await db.scalar(select(func.count()).select_from(Integration)) == 0


@pytest.mark.parametrize("platform", PLATFORMS)
async def test_reselecting_type_preserves_rates_and_removing_only_affects_linked_store(
    client, db, editor, platform,
):
    store_id = await _store(client, platform)
    other_id = await _store(client, platform)
    first = await _select(client, store_id, "mala")
    other = await _select(client, other_id, "mala")
    response = await client.patch(f'/api/pricing/accounts/{first["id"]}', json={
        "commission": "0.17", "margin1": "0.31", "shipping1": "12.50",
    })
    assert response.status_code == 200, response.text
    repeated = await _select(client, store_id, "mala")
    assert repeated["id"] == first["id"]
    assert Decimal(str(repeated["commission"])) == Decimal("0.17")
    assert Decimal(str(repeated["margin1"])) == Decimal("0.31")
    assert Decimal(str(repeated["shipping1"])) == Decimal("12.50")
    assert await db.scalar(select(func.count()).select_from(PricingAccount)) == 2
    for _ in range(2):
        response = await client.delete(f"/api/pricing/store-info/{store_id}/department/mala")
        assert response.status_code == 204, response.text
    assert [row["id"] for row in await _accounts(client, platform)] == [other["id"]]


@pytest.mark.parametrize("platform", PLATFORMS)
async def test_archiving_store_hides_its_pricing_accounts_without_losing_them(
    client, db, editor, platform,
):
    store_id = await _store(client, platform)
    account = await _select(client, store_id, "mala")
    response = await client.post(f"/api/pricing/store-info/{store_id}/archive")
    assert response.status_code == 200, response.text
    assert await _accounts(client, platform) == []
    assert await db.get(PricingAccount, UUID(account["id"])) is not None
    response = await client.post(f"/api/pricing/store-info/{store_id}/unarchive")
    assert response.status_code == 200, response.text
    assert [row["id"] for row in await _accounts(client, platform)] == [account["id"]]


@pytest.mark.parametrize("platform", PLATFORMS)
async def test_new_platform_type_edits_and_accounts_respect_team_scope(
    client, db, editor, platform, make_user, auth_as,
):
    store_id = await _store(client, platform, sales_team=1)
    account = await _select(client, store_id, "mala")
    for permissions, teams, expected in (
        ({"lojas_info": {"view": True}}, [1], 403),
        (PERMISSIONS, [2], 404),
        (PERMISSIONS, [1], 200),
    ):
        user = await make_user(permissions=permissions)
        user.sales_teams = teams
        await db.commit()
        auth_as(user)
        response = await client.post(f"/api/pricing/store-info/{store_id}/department", json={
            "department": "mala",
        })
        assert response.status_code == expected, response.text
        if expected == 200:
            assert response.json()["id"] == account["id"]
        if permissions is PERMISSIONS:
            visible = await _accounts(client, platform)
            assert bool(visible) == (teams == [1])
    stored = await db.get(PricingAccount, UUID(account["id"]), populate_existing=True)
    assert stored.user_id == editor.id
    assert await db.scalar(select(func.count()).select_from(PricingAccount)) == 1


@pytest.mark.parametrize("platform", PLATFORMS)
async def test_concurrent_type_selections_create_one_account_per_type(
    client, db, editor, platform,
):
    store_id = await _store(client, platform)
    await asyncio.gather(*(
        _select(client, store_id, slug) for slug in ("mala", "mala", "celular", "eletro")
    ))
    accounts = await _accounts(client, platform)
    assert len(accounts) == 3
    assert {row["department"] for row in accounts} == {"mala", "celular", "eletro"}


@pytest.mark.parametrize("platform", PLATFORMS)
async def test_switching_site_to_supported_platform_converts_existing_types(
    client, db, editor, platform,
):
    store_id = await _store(client, "site")
    for slug in ("celular", "eletro"):
        await _select(client, store_id, slug)
    assert await db.scalar(select(func.count()).select_from(PricingAccount)) == 0
    response = await client.patch(f"/api/pricing/store-info/{store_id}", json={
        "platform": platform,
    })
    assert response.status_code == 200, response.text
    assert response.json()["departments"] == ["celular", "eletro"]
    assert response.json()["has_pricing"] is True
    accounts = await _accounts(client, platform)
    assert len(accounts) == 2
    assert all(row["store_info_id"] == store_id for row in accounts)
    assert all(row[field] is None for row in accounts for field in MONEY_FIELDS)
    response = await client.patch(f"/api/pricing/store-info/{store_id}", json={
        "platform": platform, "observation": "Confirmado",
    })
    assert response.status_code == 200, response.text
    assert {row["id"] for row in await _accounts(client, platform)} == {
        row["id"] for row in accounts
    }
    assert await db.scalar(select(func.count()).select_from(Integration)) == 0


@pytest.mark.parametrize("platform", PLATFORMS)
async def test_switching_platform_cannot_mislabel_existing_pricing_accounts(
    client, db, editor, platform,
):
    store_id = await _store(client, platform)
    account = await _select(client, store_id, "mala")
    other_platform = "netshoes" if platform == "carrefour" else "carrefour"
    for target in (other_platform, "site"):
        response = await client.patch(f"/api/pricing/store-info/{store_id}", json={
            "platform": target,
        })
        assert response.status_code == 400, response.text
        assert response.json()["detail"]["code"] == "store_info_platform_has_pricing_accounts"
    assert [row["id"] for row in await _accounts(client, platform)] == [account["id"]]
    response = await client.delete(f"/api/pricing/store-info/{store_id}/department/mala")
    assert response.status_code == 204, response.text
    response = await client.patch(f"/api/pricing/store-info/{store_id}", json={
        "platform": other_platform,
    })
    assert response.status_code == 200, response.text
    assert response.json()["departments"] == []
    assert await db.scalar(select(func.count()).select_from(PricingAccount)) == 0


@pytest.mark.parametrize("platform", PLATFORMS)
async def test_converted_type_does_not_reappear_after_account_deletion(
    client, db, editor, platform,
):
    store_id = await _store(client, "site")
    await _select(client, store_id, "mala")
    response = await client.patch(f"/api/pricing/store-info/{store_id}", json={
        "platform": platform,
    })
    assert response.status_code == 200, response.text
    store = await db.get(StoreInfo, UUID(store_id), populate_existing=True)
    assert store.manual_departments is None
    account = (await db.execute(select(PricingAccount))).scalar_one()
    await db.delete(account)
    await db.commit()
    response = await client.patch(f"/api/pricing/store-info/{store_id}", json={
        "platform": platform, "observation": "Editado depois",
    })
    assert response.status_code == 200, response.text
    assert response.json()["departments"] == []
    assert response.json()["has_pricing"] is False
    assert await db.scalar(select(func.count()).select_from(PricingAccount)) == 0
