"""Equipe comercial organiza cadastros sem alterar o acesso individual."""
# ruff: noqa: S608
# SQL usa apenas nomes locais constantes e um schema temporário gerado por UUID.

import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.deps.team_scope import resolve_team_scope
from app.models import StoreInfo, User, UserRole
from app.schemas.pricing import StoreInfoCreate, StoreInfoPatch
from app.schemas.users import UserCreate, UserPatch


@pytest.mark.parametrize(
    ("schema", "required"),
    [
        (UserCreate, {"email": "team@davinci-test.com"}),
        (UserPatch, {}),
        (StoreInfoCreate, {"platform": "shopee"}),
        (StoreInfoPatch, {}),
    ],
)
def test_commercial_team_accepts_only_integer_one_two_or_null(schema, required):
    for value in (1, 2, None):
        assert schema(**required, commercial_team=value).commercial_team == value
    for value in (-1, 0, 3, 101, True, False, 1.0, "1", "1.1"):
        with pytest.raises(ValidationError):
            schema(**required, commercial_team=value)


@pytest.mark.parametrize("schema", [UserPatch, StoreInfoPatch])
def test_commercial_team_patch_does_not_include_access_fields(schema):
    assert schema(commercial_team=2).model_dump(exclude_unset=True) == {"commercial_team": 2}
    assert schema(commercial_team=None).model_dump(exclude_unset=True) == {"commercial_team": None}
    assert "commercial_team" not in schema().model_dump(exclude_unset=True)


@pytest.mark.asyncio
async def test_patch_user_commercial_team_preserves_access(client, make_user, auth_as, db):
    admin = await make_user(role=UserRole.ADMIN)
    target = await make_user(
        permissions={
            "lojas_info": {"view": True, "edit": False, "delete": False},
            "tabela_precos_todas_contas": {"view": True, "edit": False, "delete": False},
        }
    )
    target.sales_teams = [101, 10001]
    target.stock_tags = ["sa"]
    target.marketing_teams = ["video"]
    target.commercial_team = 1
    visible = StoreInfo(user_id=admin.id, platform="ml", sales_team=101, commercial_team=1)
    hidden = StoreInfo(user_id=admin.id, platform="ml", sales_team=102, commercial_team=1)
    db.add_all([visible, hidden])
    await db.commit()
    old_permissions = deepcopy(target.permissions)
    auth_as(admin)

    for value in (2, None):
        response = await client.patch(f"/api/users/{target.id}", json={"commercial_team": value})
        assert response.status_code == 200, response.text
        assert response.json()["commercial_team"] == value
        await db.refresh(target)
        assert target.sales_teams == [101, 10001]
        assert target.stock_tags == ["sa"]
        assert target.marketing_teams == ["video"]
        assert target.permissions == old_permissions
        scope = await resolve_team_scope(db, target)
        assert scope.unrestricted is False
        assert scope.store_info_ids == {visible.id}


@pytest.mark.asyncio
async def test_patch_store_commercial_team_preserves_access(client, make_user, auth_as, db):
    admin = await make_user(role=UserRole.ADMIN)
    target = await make_user()
    target.sales_teams = [101]
    target.commercial_team = 1
    store = StoreInfo(user_id=admin.id, platform="shopee", sales_team=101, commercial_team=1)
    db.add(store)
    await db.commit()
    auth_as(admin)

    for value in (2, None):
        response = await client.patch(
            f"/api/pricing/store-info/{store.id}", json={"commercial_team": value}
        )
        assert response.status_code == 200, response.text
        assert response.json()["commercial_team"] == value
        assert response.json()["sales_team"] == 101
        await db.refresh(store)
        await db.refresh(target)
        assert store.sales_team == 101
        assert target.sales_teams == [101]
        assert target.commercial_team == 1
        scope = await resolve_team_scope(db, target)
        assert scope.unrestricted is False
        assert scope.store_info_ids == {store.id}


@pytest.mark.asyncio
async def test_create_commercial_team_does_not_invent_access_codes(client, make_user, auth_as, db):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    response = await client.post(
        "/api/users", json={"email": "commercial-new@davinci-test.com", "commercial_team": 2}
    )
    assert response.status_code == 201, response.text
    assert response.json()["commercial_team"] == 2
    assert response.json()["sales_teams"] is None
    user = await db.get(User, UUID(response.json()["id"]))
    assert user.commercial_team == 2
    assert user.sales_teams is None

    response = await client.post(
        "/api/pricing/store-info", json={"platform": "shopee", "commercial_team": 2}
    )
    assert response.status_code == 201, response.text
    assert response.json()["commercial_team"] == 2
    assert response.json()["sales_team"] is None
    store = await db.get(StoreInfo, UUID(response.json()["id"]))
    assert store.commercial_team == 2
    assert store.sales_team is None


@pytest.mark.asyncio
async def test_migration_preserves_legacy_data_and_can_drop_only_new_columns(db):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0263_commercial_team.py"
    spec = importlib.util.spec_from_file_location("commercial_team_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    schema = f"commercial_team_test_{uuid4().hex}"
    migration.SCHEMA = schema

    def run_migration(sync_session, direction):
        migration.op = Operations(MigrationContext.configure(sync_session.connection()))
        getattr(migration, direction)()

    # Tiny temporary tables keep migration coverage independent of the app's
    # evolving schema. All DDL runs in the disposable test database.
    await db.execute(text(f"CREATE SCHEMA {schema}"))
    try:
        await db.execute(text(
            f"CREATE TABLE {schema}.users (id integer PRIMARY KEY, sales_teams jsonb, "
            "permissions jsonb, stock_tags jsonb)"
        ))
        await db.execute(text(
            f"CREATE TABLE {schema}.store_info (id integer PRIMARY KEY, sales_team integer)"
        ))
        cases = [
            (1, [101, 102], 1),
            (2, [201, 10001], 2),
            (3, [101, 201], None),
            (4, None, None),
            (5, [], None),
            (6, [10001], None),
            (7, [100, 200, 300], None),
            (8, [199], 1),
            (9, [299], 2),
        ]
        for user_id, access, _ in cases:
            await db.execute(text(
                f"INSERT INTO {schema}.users VALUES "
                "(:id, CAST(:access AS jsonb), CAST(:permissions AS jsonb), '[\"sa\"]'::jsonb)"
            ), {
                "id": user_id,
                "access": json.dumps(access) if access is not None else None,
                "permissions": json.dumps({"lojas_info": {"view": True}}),
            })
        await db.execute(text(
            f"INSERT INTO {schema}.store_info VALUES (1, 101), (2, 201), (3, 10001), (4, NULL)"
        ))

        async def snapshot(table):
            result = await db.execute(text(f"SELECT * FROM {schema}.{table} ORDER BY id"))
            return [dict(row) for row in result.mappings()]

        original_users = await snapshot("users")
        original_stores = await snapshot("store_info")
        await db.run_sync(run_migration, "upgrade")
        upgraded_users = await snapshot("users")
        assert [row.pop("commercial_team") for row in upgraded_users] == [c[2] for c in cases]
        assert upgraded_users == original_users
        upgraded_stores = await snapshot("store_info")
        assert [row.pop("commercial_team") for row in upgraded_stores] == [1, 1, 1, None]
        assert upgraded_stores == original_stores

        for table in ("users", "store_info"):
            with pytest.raises(IntegrityError):
                async with db.begin_nested():
                    await db.execute(text(
                        f"UPDATE {schema}.{table} SET commercial_team = 3 WHERE id = 1"
                    ))

        await db.run_sync(run_migration, "downgrade")
        assert await snapshot("users") == original_users
        assert await snapshot("store_info") == original_stores
    finally:
        await db.execute(text(f"DROP SCHEMA {schema} CASCADE"))
