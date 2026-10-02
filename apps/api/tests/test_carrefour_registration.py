"""Cadastro manual Carrefour sem criar integração nem regras de preço."""

# SQL de teste: identificador gerado por uuid4, sem entrada externa.
# ruff: noqa: S608

import importlib.util
from pathlib import Path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, select, text

from app.models import (
    Cadastro,
    CadastroTipo,
    Company,
    Integration,
    PricingAccount,
    StoreInfo,
    UserRole,
)


async def test_carrefour_store_info_create_edit_list_without_automatic_integration(
    client, db, make_user, auth_as,
):
    auth_as(await make_user(role=UserRole.ADMIN))
    response = await client.post("/api/pricing/store-info", json={
        "platform": "carrefour", "account_name": "loja manual", "email": "a@example.com",
    })
    assert response.status_code == 201, response.text
    store_id = response.json()["id"]
    response = await client.patch(f"/api/pricing/store-info/{store_id}", json={
        "account_name": "loja Carrefour", "platform": "carrefour",
    })
    assert response.status_code == 200, response.text
    stores = (await client.get("/api/pricing/store-info")).json()
    store = next(row for row in stores if row["id"] == store_id)
    assert store["platform"] == "carrefour"
    assert store["account_name"] == "loja Carrefour"
    assert store["has_pricing"] is False
    assert store["has_integration"] is False
    assert await db.scalar(select(func.count()).select_from(Integration)) == 0
    assert await db.scalar(select(func.count()).select_from(PricingAccount)) == 0


async def test_carrefour_account_in_company_and_cadastro_grids_with_resource_reservations(
    client, db, make_user, auth_as,
):
    auth_as(await make_user(role=UserRole.ADMIN))
    company = Company(razao_social="Carrefour teste", apelido="conta Carrefour")
    resources = [Cadastro(tipo=tipo, codigo=f"carrefour-{tipo.value}") for tipo in (
        CadastroTipo.FONE, CadastroTipo.EMAIL, CadastroTipo.SERVIDOR,
    )]
    db.add_all([company, *resources])
    await db.commit()
    assert "carrefour" in company.enabled_marketplaces
    response = await client.post("/api/stores/account", json={
        "company_id": str(company.id), "marketplace": "carrefour",
        "phone_id": str(resources[0].id), "email_id": str(resources[1].id),
        "server_id": str(resources[2].id),
    })
    assert response.status_code == 201, response.text
    store_id = response.json()["id"]
    assert response.json()["integration_id"] is None
    filtered = await client.get("/api/stores", params={"marketplace": "carrefour"})
    assert [row["id"] for row in filtered.json()] == [store_id]
    companies = (await client.get("/api/companies/grid")).json()
    assert "carrefour" in companies["marketplaces"]
    row = next(row for row in companies["rows"] if row["company"]["id"] == str(company.id))
    assert row["stores"]["carrefour"]["id"] == store_id
    assert row["stores"]["carrefour"]["status"] == "active"
    info = (await db.execute(select(StoreInfo))).scalar_one()
    assert info.platform == "carrefour"
    for resource in resources:
        response = await client.get("/api/cadastros/grid", params={"tipo": resource.tipo.value})
        grid = response.json()
        assert "carrefour" in grid["marketplaces"]
        cad_row = next(row for row in grid["rows"] if row["cadastro"]["id"] == str(resource.id))
        assert len(cad_row["cells"]["carrefour"]) == 1
        for marketplace in ("carrefour", "ml"):
            available = await client.get("/api/cadastros/available", params={
                "tipo": resource.tipo.value, "marketplace": marketplace,
            })
            assert available.status_code == 200, available.text
            free = marketplace == "ml" and resource.tipo != CadastroTipo.SERVIDOR
            assert (str(resource.id) in {row["id"] for row in available.json()}) == free
    assert await db.scalar(select(func.count()).select_from(Integration)) == 0
    assert await db.scalar(select(func.count()).select_from(PricingAccount)) == 0


async def test_disabled_carrefour_stays_blocked(client, db, make_user, auth_as):
    auth_as(await make_user(role=UserRole.ADMIN))
    company = Company(razao_social="Restrita", apelido="restrita", enabled_marketplaces=["ml"])
    db.add(company)
    await db.commit()
    response = await client.post("/api/stores", json={
        "company_id": str(company.id), "marketplace": "carrefour",
    })
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "marketplace_not_enabled"


async def test_carrefour_migration_preserves_restrictions_and_is_idempotent(db, monkeypatch):
    """Executa a migração real num schema descartável com o enum anterior."""
    path = Path(__file__).parents[1] / "alembic/versions/0357_marketplace_carrefour.py"
    spec = importlib.util.spec_from_file_location("carrefour_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    schema = "carrefour_migration_" + uuid4().hex
    monkeypatch.setattr(migration, "SCHEMA", schema)
    old = list(migration.PREVIOUS_MARKETPLACES)
    rows = {"default": old, "restricted": ["ml", "site"], "empty": [],
            "already": [*old, "carrefour"]}

    def run_upgrade(session):
        migration.op = Operations(MigrationContext.configure(session.connection()))
        migration.upgrade()

    try:
        await db.execute(text(f'CREATE SCHEMA "{schema}"'))
        await db.execute(text(f'CREATE TYPE "{schema}".marketplace AS ENUM '
                              "(" + ",".join(f"'{m}'" for m in old) + ")"))
        await db.execute(text(f'CREATE TABLE "{schema}".companies '
                              '(name text PRIMARY KEY, enabled_marketplaces text[] NOT NULL)'))
        await db.execute(text(f'CREATE TABLE "{schema}".stores '
                              f'(marketplace "{schema}".marketplace NOT NULL)'))
        for name, enabled in rows.items():
            await db.execute(text(f'INSERT INTO "{schema}".companies VALUES (:name, :enabled)'),
                             {"name": name, "enabled": enabled})
        await db.commit()
        await db.run_sync(run_upgrade)
        await db.commit()
        await db.run_sync(run_upgrade)
        await db.commit()
        await db.execute(text(f'INSERT INTO "{schema}".stores VALUES (\'carrefour\')'))
        await db.execute(text(f'INSERT INTO "{schema}".companies (name) VALUES (\'new\')'))
        actual = dict((await db.execute(text(
            f'SELECT name, enabled_marketplaces FROM "{schema}".companies'
        ))).all())
        assert actual["restricted"] == rows["restricted"]
        assert actual["empty"] == []
        assert actual["already"] == rows["already"]
        assert actual["default"] == [*old, "carrefour"]
        assert actual["new"] == [*old, "carrefour"]
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await db.commit()
