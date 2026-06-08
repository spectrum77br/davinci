import pytest

from app.models import UserRole, UserStatus


@pytest.mark.asyncio
async def test_list_users_requires_admin(client, make_user, auth_as):
    user = await make_user(role=UserRole.USER)
    auth_as(user)
    r = await client.get("/api/users")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "admin_only"


@pytest.mark.asyncio
async def test_list_users_admin_ok(client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN, email="admin1@davinci-test.com")
    await make_user(email="alice@davinci-test.com")
    await make_user(email="bob@davinci-test.com")
    auth_as(admin)
    r = await client.get("/api/users")
    assert r.status_code == 200
    emails = {u["email"] for u in r.json()["items"]}
    assert {"admin1@davinci-test.com", "alice@davinci-test.com", "bob@davinci-test.com"} <= emails


@pytest.mark.asyncio
async def test_create_user_nasce_user_pending(client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    r = await client.post(
        "/api/users",
        json={"email": "novo@davinci-test.com", "name": "Novo", "tuta": "novo@tuta"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["role"] == "user"
    assert body["status"] == "pending"
    assert body["email"] == "novo@davinci-test.com"
    assert body["tuta"] == "novo@tuta"


@pytest.mark.asyncio
async def test_create_user_email_conflict(client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    await make_user(email="dup@davinci-test.com")
    auth_as(admin)
    r = await client.post("/api/users", json={"email": "dup@davinci-test.com"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "email_exists"


@pytest.mark.asyncio
async def test_patch_user_ignores_role_field(client, make_user, auth_as, db):
    admin = await make_user(role=UserRole.ADMIN)
    target = await make_user(email="target@davinci-test.com", role=UserRole.USER)
    auth_as(admin)
    # Sending `role` should be silently ignored by UserPatch (extra="ignore" default).
    r = await client.patch(
        f"/api/users/{target.id}",
        json={"name": "Renamed", "role": "admin"},
    )
    assert r.status_code == 200
    assert r.json()["role"] == "user"
    assert r.json()["name"] == "Renamed"
    await db.refresh(target)
    assert target.role == UserRole.USER


@pytest.mark.asyncio
async def test_approve_pending_user(client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    pending = await make_user(email="pen@davinci-test.com", status=UserStatus.PENDING)
    auth_as(admin)
    r = await client.patch(f"/api/users/{pending.id}", json={"status": "active"})
    assert r.status_code == 200
    assert r.json()["status"] == "active"


@pytest.mark.asyncio
async def test_patch_permissions_cascade(client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    target = await make_user(email="t@davinci-test.com")
    auth_as(admin)
    payload = {
        "permissions": {
            "produtos": {"delete": True, "view": False, "edit": False},
            "anuncios": {"edit": True, "view": False},
        }
    }
    r = await client.patch(f"/api/users/{target.id}/permissions", json=payload)
    assert r.status_code == 200, r.text
    perms = r.json()["permissions"]
    assert perms["produtos"] == {"view": True, "edit": True, "delete": True}
    assert perms["anuncios"] == {"view": True, "edit": True, "delete": False}
    assert perms["tabela_precos"] == {"view": False, "edit": False, "delete": False}


@pytest.mark.asyncio
async def test_cannot_edit_admin_permissions(client, make_user, auth_as):
    admin1 = await make_user(role=UserRole.ADMIN, email="a1@davinci-test.com")
    admin2 = await make_user(role=UserRole.ADMIN, email="a2@davinci-test.com")
    auth_as(admin1)
    r = await client.patch(
        f"/api/users/{admin2.id}/permissions",
        json={"permissions": {"produtos": {"view": True}}},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "cannot_edit_admin_permissions"


@pytest.mark.asyncio
async def test_cannot_delete_self(client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    r = await client.delete(f"/api/users/{admin.id}")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "cannot_delete_self"


# NOTE: the `last_admin` guard on DELETE is structurally unreachable —
# `require_admin` forces caller=admin, and self-delete blocks caller==target,
# so after exclude(target_id) the count is always >=1. The guard is kept as
# defense-in-depth; the reachable path is exercised below via PATCH status.


@pytest.mark.asyncio
async def test_last_admin_guard_via_status_patch(client, make_user, auth_as):
    """The reachable last-admin guard is on PATCH status: an admin demoting
    itself (or another last admin) to suspended/pending must be blocked."""
    a1 = await make_user(role=UserRole.ADMIN, email="solo@davinci-test.com")
    auth_as(a1)
    r = await client.patch(f"/api/users/{a1.id}", json={"status": "suspended"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "last_admin"


@pytest.mark.asyncio
async def test_me_permissions(client, make_user, auth_as):
    user = await make_user(
        role=UserRole.USER,
        permissions={"produtos": {"view": True, "edit": True, "delete": False}},
    )
    auth_as(user)
    r = await client.get("/api/users/me/permissions")
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "user"
    assert body["is_admin"] is False
    assert body["permissions"]["produtos"] == {"view": True, "edit": True, "delete": False}
    assert body["permissions"]["anuncios"] == {"view": False, "edit": False, "delete": False}


@pytest.mark.asyncio
async def test_me_permissions_admin_bypass(client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    r = await client.get("/api/users/me/permissions")
    assert r.status_code == 200
    assert r.json()["is_admin"] is True


@pytest.mark.asyncio
async def test_create_user_with_password(client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    r = await client.post(
        "/api/users",
        json={"email": "comsenha@davinci-test.com", "password": "segredo123"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["has_password"] is True


@pytest.mark.asyncio
async def test_create_user_password_too_short(client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    r = await client.post(
        "/api/users",
        json={"email": "curta@davinci-test.com", "password": "123"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "password_too_short"


@pytest.mark.asyncio
async def test_admin_sets_user_password(client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    target = await make_user(email="semsenha@davinci-test.com")
    auth_as(admin)

    # Antes: sem senha.
    r = await client.get(f"/api/users/{target.id}")
    assert r.json()["has_password"] is False

    r = await client.post(
        f"/api/users/{target.id}/password",
        json={"password": "novasenha123"},
    )
    assert r.status_code == 204, r.text

    # Depois: com senha.
    r = await client.get(f"/api/users/{target.id}")
    assert r.json()["has_password"] is True


@pytest.mark.asyncio
async def test_set_password_too_short(client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    target = await make_user(email="alvo@davinci-test.com")
    auth_as(admin)
    r = await client.post(
        f"/api/users/{target.id}/password",
        json={"password": "abc"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "password_too_short"


@pytest.mark.asyncio
async def test_cannot_set_other_admin_password(client, make_user, auth_as):
    admin1 = await make_user(role=UserRole.ADMIN, email="adm1@davinci-test.com")
    admin2 = await make_user(role=UserRole.ADMIN, email="adm2@davinci-test.com")
    auth_as(admin1)
    r = await client.post(
        f"/api/users/{admin2.id}/password",
        json={"password": "tentativa123"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "cannot_set_admin_password"


@pytest.mark.asyncio
async def test_delete_user_soft_deletes(client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    target = await make_user(email="del@davinci-test.com")
    auth_as(admin)
    r = await client.delete(f"/api/users/{target.id}")
    assert r.status_code == 204

    # By default list excludes disabled.
    r = await client.get("/api/users")
    emails = {u["email"] for u in r.json()["items"]}
    assert "del@davinci-test.com" not in emails

    # include_disabled=true brings it back, with status=suspended.
    r = await client.get("/api/users?include_disabled=true")
    rows = {u["email"]: u for u in r.json()["items"]}
    assert "del@davinci-test.com" in rows
    assert rows["del@davinci-test.com"]["status"] == "suspended"
    assert rows["del@davinci-test.com"]["disabled_at"] is not None
