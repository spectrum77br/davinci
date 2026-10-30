"""Store edits require an administrator, including indirect routes in Empresas."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.deps.auth import require_admin
from app.main import app
from app.models import (
    Cadastro,
    CadastroStore,
    CadastroTipo,
    Company,
    Integration,
    IntegrationPlatform,
    Marketplace,
    PricingAccount,
    PricingPlatform,
    Segment,
    Store,
    StoreInfo,
    UserRole,
)
from app.security.cipher import encrypt

WRITES = (
    "create", "patch", "password", "archive", "unarchive", "department",
    "remove_department", "delete", "create_account", "account_email", "delete_account",
)
ALL_PERMISSIONS = {
    name: {"view": True, "edit": True, "delete": True}
    for name in ("lojas_info", "empresa", "cadastro", "tabela_precos_contas")
}


async def _seed(db, make_user, operation):
    owner = await make_user(role=UserRole.ADMIN)
    company = Company(razao_social="Existente", apelido="existente")
    new_company = Company(razao_social="Nova", apelido="nova")
    segments = [Segment(name=slug.title(), slug=slug) for slug in ("mala", "celular")]
    codes = [Cadastro(tipo=kind, codigo=f"livre-{kind.value}") for kind in (
        CadastroTipo.FONE, CadastroTipo.EMAIL, CadastroTipo.SERVIDOR,
    )]
    integration = Integration(
        user_id=owner.id, platform=IntegrationPlatform.SHOPEE,
        name="existente", credentials=b"unused-test-credentials",
    )
    db.add_all([company, new_company, integration, *segments, *codes])
    await db.flush()
    store = Store(company_id=company.id, marketplace=Marketplace.SHOPEE)
    info = StoreInfo(
        user_id=owner.id, platform="shopee", account_name="existente",
        email="original", password_enc=encrypt("original-password"),
        integration_id=integration.id, sales_team=1,
    )
    if operation == "unarchive":
        info.archived_at = integration.archived_at = datetime.now(UTC)
    db.add_all([store, info])
    await db.flush()
    db.add(PricingAccount(
        user_id=owner.id, name="existente", platform=PricingPlatform.SHOPEE,
        store_info_id=info.id, segment_id=segments[0].id,
    ))
    await db.commit()
    base = f"/api/pricing/store-info/{info.id}"
    requests = {
        "create": ("POST", "/api/pricing/store-info", {"platform": "site"}, 201),
        "patch": ("PATCH", base, {"account_name": "changed", "sales_team": 2}, 200),
        "password": ("PATCH", base, {"password": "changed-password"}, 200),
        "archive": ("POST", f"{base}/archive", None, 200),
        "unarchive": ("POST", f"{base}/unarchive", None, 200),
        "department": ("POST", f"{base}/department", {"department": "celular"}, 200),
        "remove_department": ("DELETE", f"{base}/department/mala", None, 204),
        "delete": ("DELETE", base, None, 204),
        "create_account": ("POST", "/api/stores/account", {
            "company_id": str(new_company.id), "marketplace": "ml",
            "phone_id": str(codes[0].id), "email_id": str(codes[1].id),
            "server_id": str(codes[2].id),
        }, 201),
        "account_email": ("PUT", "/api/stores/account/email", {
            "company_id": str(company.id), "marketplace": "shopee", "email": "changed",
        }, 200),
        "delete_account": ("DELETE", f"/api/stores/{store.id}", None, 204),
    }
    return requests[operation]


async def _snapshot(db):
    # Include related tables: a rejected request must not reserve codes, alter
    # price accounts, or archive an integration before the authorization fails.
    return {
        model.__name__: (await db.execute(
            select(*model.__table__.columns).order_by(*model.__table__.primary_key.columns)
        )).all()
        for model in (StoreInfo, Store, PricingAccount, Integration, Cadastro, CadastroStore)
    }


@pytest.mark.parametrize("operation", WRITES)
@pytest.mark.parametrize("granted_edits", [False, True])
async def test_non_admin_cannot_mutate_stores_even_with_edit_and_delete_grants(
    db, client, make_user, auth_as, operation, granted_edits,
):
    method, path, payload, _ = await _seed(db, make_user, operation)
    user = await make_user(permissions=ALL_PERMISSIONS if granted_edits else {
        "lojas_info": {"view": True},
    })
    auth_as(user)
    assert require_admin not in app.dependency_overrides
    before = await _snapshot(db)
    response = await client.request(method, path, json=payload)
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "admin_only"
    assert await _snapshot(db) == before


@pytest.mark.parametrize("operation", WRITES)
async def test_admin_can_mutate_stores_without_individual_edit_grants(
    db, client, make_user, auth_as, operation,
):
    method, path, payload, expected_status = await _seed(db, make_user, operation)
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    # Exercise the actual role dependency for administrators too.
    app.dependency_overrides.pop(require_admin, None)
    before = await _snapshot(db)
    response = await client.request(method, path, json=payload)
    assert response.status_code == expected_status, response.text
    assert await _snapshot(db) != before


async def test_view_only_user_can_read_and_copy_password_only_within_team_scope(
    db, client, make_user, auth_as,
):
    user = await make_user(permissions={"lojas_info": {"view": True}})
    user.sales_teams = [1]
    visible = StoreInfo(
        user_id=user.id, platform="site", account_name="Visible", sales_team=1,
        password_enc=encrypt("visible-password"),
    )
    hidden = StoreInfo(
        user_id=user.id, platform="site", account_name="Other team", sales_team=2,
        password_enc=encrypt("hidden-password"),
    )
    db.add_all([visible, hidden])
    await db.commit()
    auth_as(user)
    response = await client.get("/api/pricing/store-info")
    assert response.status_code == 200, response.text
    assert {row["id"] for row in response.json()} == {str(visible.id)}
    assert "password" not in response.json()[0]
    response = await client.get(f"/api/pricing/store-info/{visible.id}/password")
    assert response.status_code == 200, response.text
    assert response.json() == {"password": "visible-password"}
    response = await client.get(f"/api/pricing/store-info/{hidden.id}/password")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "store_info_not_found"


async def test_edit_grant_without_view_does_not_grant_password_access(
    db, client, make_user, auth_as,
):
    user = await make_user(permissions={"lojas_info": {"edit": True, "delete": True}})
    info = StoreInfo(user_id=user.id, platform="site", password_enc=encrypt("private"))
    db.add(info)
    await db.commit()
    auth_as(user)
    for path in ("/api/pricing/store-info", f"/api/pricing/store-info/{info.id}/password"):
        response = await client.get(path)
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["action"] == "view"


async def test_company_deletion_does_not_delete_or_change_store_info(
    db, client, make_user, auth_as,
):
    await _seed(db, make_user, "delete_account")
    company = await db.scalar(select(Company).where(Company.apelido == "existente"))
    store = await db.scalar(select(Store).where(Store.company_id == company.id))
    integration = await db.scalar(select(Integration))
    integration.store_id = store.id
    await db.commit()
    before = (await db.execute(select(*StoreInfo.__table__.columns))).all()
    user = await make_user(permissions={
        "empresa": {"view": True, "delete": True},
        "lojas_info": {"view": True},
    })
    auth_as(user)
    response = await client.delete(f"/api/companies/{company.id}")
    assert response.status_code == 204, response.text
    assert await db.scalar(select(Company.id).where(Company.id == company.id)) is None
    assert await db.scalar(select(Store.id).where(Store.id == store.id)) is None
    # Company -> Store cascades, but StoreInfo is an independent record.
    assert (await db.execute(select(*StoreInfo.__table__.columns))).all() == before
    await db.refresh(integration)
    assert integration.store_id is None
