"""Nova conta reserva cadastros sem reutilização ou gravações parciais."""
import asyncio

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.models import Cadastro, CadastroStore, CadastroTipo, Company, Store, StoreInfo, UserRole


async def seed(db, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    company = Company(razao_social="Empresa de teste", apelido="nova conta")
    rows = [Cadastro(tipo=tipo, codigo=f"{tipo.value}-1") for tipo in (
        CadastroTipo.FONE, CadastroTipo.EMAIL, CadastroTipo.SERVIDOR,
    )]
    db.add_all([company, *rows])
    await db.commit()
    return admin, company, rows, {
        "company_id": str(company.id), "marketplace": "ml",
        "phone_id": str(rows[0].id), "email_id": str(rows[1].id),
        "server_id": str(rows[2].id),
    }


async def counts(db):
    return [await db.scalar(select(func.count()).select_from(model))
            for model in (Store, StoreInfo, CadastroStore)]


@pytest.mark.asyncio
async def test_account_reserves_codes_only_on_selected_marketplace(db, client, make_user, auth_as):
    admin, company, rows, body = await seed(db, make_user, auth_as)
    response = await client.post("/api/stores/account", json=body)
    assert response.status_code == 201, response.text
    assert await counts(db) == [1, 1, 3]
    info = (await db.execute(select(StoreInfo))).scalar_one()
    assert (info.phone, info.email, info.server) == tuple(row.codigo for row in rows)
    assert info.account_name == company.apelido
    assert info.user_id == admin.id
    for row in rows:
        for marketplace in ("ml", "shopee"):
            response = await client.get("/api/cadastros/available", params={
                "tipo": row.tipo.value, "marketplace": marketplace,
            })
            assert response.status_code == 200
            ids = {r["id"] for r in response.json()}
            assert (str(row.id) in ids) == (marketplace == "shopee")


@pytest.mark.asyncio
@pytest.mark.parametrize("index", [0, 1, 2])
async def test_account_rechecks_stale_selection_without_partial_store(
    db, client, make_user, auth_as, index,
):
    _, _, rows, body = await seed(db, make_user, auth_as)
    response = await client.get("/api/cadastros/available", params={
        "tipo": rows[index].tipo.value, "marketplace": "ml",
    })
    assert str(rows[index].id) in {r["id"] for r in response.json()}
    rows[index].raw_links = {"ml": "outra conta"}
    await db.commit()
    response = await client.post("/api/stores/account", json=body)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "cadastro_unavailable"
    assert await counts(db) == [0, 0, 0]


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["empresa", "cadastro", "lojas_info"])
async def test_account_checks_all_permissions_before_writing(
    db, client, make_user, auth_as, missing,
):
    _, _, _, body = await seed(db, make_user, auth_as)
    user = await make_user(role=UserRole.USER, permissions={
        resource: {"view": True, "edit": True}
        for resource in ("empresa", "cadastro", "lojas_info") if resource != missing
    })
    auth_as(user)
    response = await client.post("/api/stores/account", json=body)
    assert response.status_code == 403
    assert await counts(db) == [0, 0, 0]


@pytest.mark.asyncio
async def test_account_rejects_mismatched_cadastro_type(db, client, make_user, auth_as):
    _, _, _, body = await seed(db, make_user, auth_as)
    body["email_id"] = body["phone_id"]
    response = await client.post("/api/stores/account", json=body)
    assert response.status_code == 409
    assert await counts(db) == [0, 0, 0]


@pytest.mark.asyncio
async def test_account_rejects_disabled_marketplace(db, client, make_user, auth_as):
    _, company, _, body = await seed(db, make_user, auth_as)
    company.enabled_marketplaces = ["amazon"]
    await db.commit()
    response = await client.post("/api/stores/account", json=body)
    assert response.status_code == 403
    assert await counts(db) == [0, 0, 0]


@pytest.mark.asyncio
async def test_account_rejects_store_info_only_duplicate(db, client, make_user, auth_as):
    admin, _, _, body = await seed(db, make_user, auth_as)
    db.add(StoreInfo(user_id=admin.id, platform="ml", account_name="NovaConta"))
    await db.commit()
    response = await client.post("/api/stores/account", json=body)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "store_already_exists"
    assert await counts(db) == [0, 1, 0]


@pytest.mark.asyncio
@pytest.mark.parametrize("duplicate_codes", [False, True])
async def test_concurrent_accounts_cannot_reserve_same_codes(
    db, client, make_user, auth_as, duplicate_codes,
):
    _, _, rows, body = await seed(db, make_user, auth_as)
    second_company = Company(razao_social="Outra", apelido="outra")
    db.add(second_company)
    await db.commit()
    second_body = {**body, "company_id": str(second_company.id)}
    if duplicate_codes:
        copies = [Cadastro(tipo=row.tipo, codigo=f" {row.codigo.upper()} ") for row in rows]
        db.add_all(copies)
        await db.commit()
        second_body.update(zip(("phone_id", "email_id", "server_id"),
                               (str(row.id) for row in copies), strict=True))
    responses = await asyncio.wait_for(asyncio.gather(
        client.post("/api/stores/account", json=body),
        client.post("/api/stores/account", json=second_body),
    ), timeout=10)
    assert sorted(r.status_code for r in responses) == [201, 409]
    assert await counts(db) == [1, 1, 3]


@pytest.mark.asyncio
async def test_store_info_write_failure_rolls_back_account_and_links(
    db, client, make_user, auth_as,
):
    _, _, _, body = await seed(db, make_user, auth_as)
    # Disposable test DB: force a later write to fail after Store was flushed.
    await db.execute(text(
        "ALTER TABLE davinci_test.store_info ADD CONSTRAINT test_reject_account "
        "CHECK (email != 'email-1')"
    ))
    await db.commit()
    try:
        with pytest.raises(IntegrityError):
            await client.post("/api/stores/account", json=body)
        assert await counts(db) == [0, 0, 0]
    finally:
        await db.execute(text(
            "ALTER TABLE davinci_test.store_info DROP CONSTRAINT test_reject_account"
        ))
        await db.commit()
