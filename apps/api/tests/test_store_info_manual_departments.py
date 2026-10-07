"""Tipo em lojas manuais persiste sem criar contas de preço ou integrações."""

# Os identificadores SQL abaixo são schemas descartáveis gerados por uuid4.
# ruff: noqa: S608

import asyncio
import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, select, text

from app.models import Integration, NfFaturador, PricingAccount, Segment

MANUAL_PLATFORMS = ("site", "carrefour", "netshoes")
EDIT_PERMISSION = {"lojas_info": {"view": True, "edit": True, "delete": True}}


@pytest_asyncio.fixture
async def manual_editor(db, make_user, auth_as):
    user = await make_user(permissions=EDIT_PERMISSION)
    db.add_all([
        Segment(name=slug.title(), slug=slug)
        for slug in ("celular", "mala", "eletro", "catalogo")
    ])
    await db.commit()
    auth_as(user)
    return user


async def _create_store(client, platform, **extra):
    response = await client.post("/api/pricing/store-info", json={
        "platform": platform, "account_name": "Mesma conta", **extra,
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _listed(client, store_id, *, archived=False):
    response = await client.get("/api/pricing/store-info", params={"archived": archived})
    assert response.status_code == 200, response.text
    return next(row for row in response.json() if row["id"] == store_id)


def _assert_manual(row, store_id, departments):
    assert row["id"] == store_id
    assert row["departments"] == sorted(departments)
    assert row["has_pricing"] is False
    assert row["has_integration"] is False
    assert "manual_departments" not in row


async def _assert_no_automatic_accounts(db):
    assert await db.scalar(select(func.count()).select_from(PricingAccount)) == 0
    assert await db.scalar(select(func.count()).select_from(Integration)) == 0


@pytest.mark.parametrize("platform", MANUAL_PLATFORMS)
async def test_manual_types_can_be_selected_removed_and_reloaded_without_pricing(
    client, db, manual_editor, platform,
):
    store_id = await _create_store(client, platform)
    other_id = await _create_store(client, platform)
    for slug, expected in (
        ("mala", ["mala"]),
        ("celular", ["celular", "mala"]),
        ("eletro", ["celular", "eletro", "mala"]),
        ("mala", ["celular", "eletro", "mala"]),
    ):
        response = await client.post(f"/api/pricing/store-info/{store_id}/department", json={
            "department": slug,
        })
        assert response.status_code == 200, response.text
        _assert_manual(response.json(), store_id, expected)
        _assert_manual(await _listed(client, store_id), store_id, expected)
    # Catálogo deixou de ser tipo de loja (06/10/2026): recusado, nada muda.
    response = await client.post(f"/api/pricing/store-info/{store_id}/department", json={
        "department": "catalogo",
    })
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "departamento_invalido"
    _assert_manual(await _listed(client, store_id), store_id, ["celular", "eletro", "mala"])

    # A mesma plataforma e o mesmo nome não compartilham a seleção manual.
    _assert_manual(await _listed(client, other_id), other_id, [])
    for slug, expected in (
        ("mala", ["celular", "eletro"]),
        ("mala", ["celular", "eletro"]),
        ("eletro", ["celular"]),
        ("celular", []),
    ):
        response = await client.delete(f"/api/pricing/store-info/{store_id}/department/{slug}")
        assert response.status_code == 204, response.text
        _assert_manual(await _listed(client, store_id), store_id, expected)
    await _assert_no_automatic_accounts(db)


@pytest.mark.parametrize("platform", MANUAL_PLATFORMS)
async def test_manual_types_survive_patch_and_archive_without_changing_invoice_rules(
    client, db, manual_editor, platform,
):
    default = NfFaturador(nome="Padrão", modo="bling", nf_cheia=True)
    per_type = NfFaturador(nome="Por tipo", modo="upseller", nf_cheia=True)
    db.add_all([default, per_type])
    await db.commit()
    invoice_rules = {"mala": str(per_type.id)}
    store_id = await _create_store(client, platform, nf_faturador_id=str(default.id),
                                  nf_faturador_por_tipo=invoice_rules)
    for slug in ("mala", "eletro"):
        response = await client.post(f"/api/pricing/store-info/{store_id}/department", json={
            "department": slug,
        })
        assert response.status_code == 200, response.text
    response = await client.patch(f"/api/pricing/store-info/{store_id}", json={
        "observation": "Conferido", "account_name": "Novo nome",
    })
    assert response.status_code == 200, response.text
    _assert_manual(response.json(), store_id, ["eletro", "mala"])
    assert response.json()["nf_faturador_id"] == str(default.id)
    assert response.json()["nf_faturador_por_tipo"] == invoice_rules

    response = await client.post(f"/api/pricing/store-info/{store_id}/archive")
    assert response.status_code == 200, response.text
    _assert_manual(response.json(), store_id, ["eletro", "mala"])
    active_stores = (await client.get("/api/pricing/store-info")).json()
    assert all(row["id"] != store_id for row in active_stores)
    _assert_manual(await _listed(client, store_id, archived=True), store_id, ["eletro", "mala"])
    response = await client.post(f"/api/pricing/store-info/{store_id}/unarchive")
    assert response.status_code == 200, response.text
    _assert_manual(response.json(), store_id, ["eletro", "mala"])

    # Retirar a classificação não apaga a regra fiscal cadastrada.
    response = await client.delete(f"/api/pricing/store-info/{store_id}/department/mala")
    assert response.status_code == 204, response.text
    row = await _listed(client, store_id)
    _assert_manual(row, store_id, ["eletro"])
    assert row["nf_faturador_id"] == str(default.id)
    assert row["nf_faturador_por_tipo"] == invoice_rules
    await _assert_no_automatic_accounts(db)


async def test_switching_between_manual_platforms_preserves_types(client, db, manual_editor):
    store_id = await _create_store(client, "site")
    response = await client.post(f"/api/pricing/store-info/{store_id}/department", json={
        "department": "mala",
    })
    assert response.status_code == 200, response.text
    for platform in ("carrefour", "netshoes", "site"):
        response = await client.patch(f"/api/pricing/store-info/{store_id}", json={
            "platform": platform,
        })
        assert response.status_code == 200, response.text
        assert response.json()["platform"] == platform
        _assert_manual(response.json(), store_id, ["mala"])
        _assert_manual(await _listed(client, store_id), store_id, ["mala"])
    await _assert_no_automatic_accounts(db)


@pytest.mark.parametrize("platform", MANUAL_PLATFORMS)
async def test_manual_types_require_valid_root_and_cannot_be_written_via_raw_patch(
    client, db, manual_editor, platform,
):
    store_id = await _create_store(client, platform, manual_departments=["unvalidated"])
    _assert_manual(await _listed(client, store_id), store_id, [])
    parent = await db.scalar(select(Segment).where(Segment.slug == "mala"))
    db.add(Segment(name="Filho", slug="mala-filha", parent_id=parent.id))
    await db.commit()
    for slug in ("does-not-exist", "mala-filha"):
        response = await client.post(f"/api/pricing/store-info/{store_id}/department", json={
            "department": slug,
        })
        assert response.status_code == 400, response.text
        assert response.json()["detail"]["code"] == "invalid_department"
        response = await client.delete(f"/api/pricing/store-info/{store_id}/department/{slug}")
        assert response.status_code == 400, response.text
    response = await client.patch(f"/api/pricing/store-info/{store_id}", json={
        "manual_departments": ["unvalidated"], "departments": ["mala"],
        "observation": "Somente este campo deve mudar",
    })
    assert response.status_code == 200, response.text
    _assert_manual(response.json(), store_id, [])
    assert response.json()["observation"] == "Somente este campo deve mudar"
    await _assert_no_automatic_accounts(db)


async def test_manual_type_edits_respect_permissions_and_store_team_scope(
    client, db, manual_editor, make_user, auth_as,
):
    store_id = await _create_store(client, "carrefour", sales_team=1)
    for permissions, teams, expected in (
        ({"lojas_info": {"view": True}}, [1], 403),
        (EDIT_PERMISSION, [2], 404),
        (EDIT_PERMISSION, [1], 200),
    ):
        user = await make_user(permissions=permissions)
        user.sales_teams = teams
        await db.commit()
        auth_as(user)
        response = await client.post(f"/api/pricing/store-info/{store_id}/department", json={
            "department": "mala",
        })
        assert response.status_code == expected, response.text
        response = await client.delete(f"/api/pricing/store-info/{store_id}/department/mala")
        assert response.status_code == (204 if expected == 200 else expected), response.text
    auth_as(manual_editor)
    _assert_manual(await _listed(client, store_id), store_id, [])
    await _assert_no_automatic_accounts(db)


async def test_unknown_platform_and_missing_store_do_not_gain_manual_type_support(
    client, db, manual_editor,
):
    store_id = await _create_store(client, "not-a-platform")
    response = await client.post(f"/api/pricing/store-info/{store_id}/department", json={
        "department": "mala",
    })
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "store_info_platform_unsupported"
    missing_id = uuid4()
    response = await client.post(f"/api/pricing/store-info/{missing_id}/department", json={
        "department": "mala",
    })
    assert response.status_code == 404, response.text
    response = await client.delete(f"/api/pricing/store-info/{missing_id}/department/mala")
    assert response.status_code == 404, response.text
    await _assert_no_automatic_accounts(db)


async def test_concurrent_manual_checkbox_edits_preserve_each_selection(
    client, db, manual_editor,
):
    store_id = await _create_store(client, "carrefour")
    endpoint = f"/api/pricing/store-info/{store_id}/department"
    response = await client.post(endpoint, json={"department": "mala"})
    assert response.status_code == 200, response.text
    responses = await asyncio.gather(
        client.post(endpoint, json={"department": "celular"}),
        client.post(endpoint, json={"department": "eletro"}),
        client.delete(f"{endpoint}/mala"),
    )
    assert [response.status_code for response in responses] == [200, 200, 204]
    _assert_manual(await _listed(client, store_id), store_id, ["celular", "eletro"])
    await _assert_no_automatic_accounts(db)


@pytest.mark.parametrize("platform", ("ml", "mercadolivre", "amazon"))
async def test_existing_pricing_platforms_keep_the_account_contract(
    client, db, manual_editor, platform,
):
    store_id = await _create_store(client, platform)
    response = await client.post(f"/api/pricing/store-info/{store_id}/department", json={
        "department": "mala",
    })
    assert response.status_code == 200, response.text
    account = response.json()
    assert account["department"] == "mala"
    assert account["store_info_id"] == store_id
    assert account["platform"] == ("mercadolivre" if platform == "ml" else platform)
    response = await client.post(f"/api/pricing/store-info/{store_id}/department", json={
        "department": "mala",
    })
    assert response.status_code == 200, response.text
    assert response.json()["id"] == account["id"]
    row = await _listed(client, store_id)
    assert row["departments"] == ["mala"]
    assert row["has_pricing"] is True
    assert await db.scalar(select(func.count()).select_from(PricingAccount)) == 1


async def test_manual_departments_migration_preserves_existing_stores(db, monkeypatch):
    """Migração real em schema isolado; nenhum acesso ao schema de produção."""
    path = Path(__file__).parents[1] / "alembic/versions/0359_store_manual_departments.py"
    spec = importlib.util.spec_from_file_location("manual_departments_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    schema = "manual_types_migration_" + uuid4().hex
    monkeypatch.setattr(migration, "SCHEMA", schema)

    def run_migration(session, direction):
        migration.op = Operations(MigrationContext.configure(session.connection()))
        getattr(migration, direction)()

    try:
        await db.execute(text(f'CREATE SCHEMA "{schema}"'))
        await db.execute(text(f'CREATE TABLE "{schema}".store_info '
                              '(id integer PRIMARY KEY, account_name text, platform text)'))
        await db.execute(text(f'INSERT INTO "{schema}".store_info VALUES '
                              "(1, 'Existente', 'carrefour')"))
        await db.commit()
        await db.run_sync(run_migration, "upgrade")
        await db.commit()
        existing = (await db.execute(text(
            f'SELECT account_name, platform, manual_departments FROM "{schema}".store_info'
        ))).one()
        assert tuple(existing) == ("Existente", "carrefour", None)
        await db.execute(text(f'UPDATE "{schema}".store_info '
                              "SET manual_departments = ARRAY['mala','eletro'] WHERE id = 1"))
        await db.execute(text(f'INSERT INTO "{schema}".store_info '
                              "(id, account_name, platform) VALUES (2, 'Nova', 'site')"))
        rows = (await db.execute(text(
            f'SELECT id, manual_departments FROM "{schema}".store_info ORDER BY id'
        ))).all()
        assert [tuple(row) for row in rows] == [(1, ["mala", "eletro"]), (2, None)]
        await db.commit()
        await db.run_sync(run_migration, "downgrade")
        await db.commit()
        rows = (await db.execute(text(
            f'SELECT id, account_name, platform FROM "{schema}".store_info ORDER BY id'
        ))).all()
        assert [tuple(row) for row in rows] == [(1, "Existente", "carrefour"), (2, "Nova", "site")]
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await db.commit()


async def test_tirar_tipo_da_loja_ml_nao_apaga_conta_sem_loja_de_mesmo_nome(
    client, db, manual_editor,
):
    """07/10/2026: no ML o clássico e o premium agora têm o nome da loja
    ("velasco"). Tirar o tipo da loja apaga só as contas LIGADAS a ela; a conta
    sem loja de mesmo nome (e os preços fixados nela) fica. Nas outras
    plataformas o casamento por nome continua como antes."""
    celular = await db.scalar(select(Segment.id).where(Segment.slug == "celular"))

    ml_store = await _create_store(client, "ml", account_name="velasco")
    r = await client.post(f"/api/pricing/store-info/{ml_store}/department", json={"department": "celular"})
    assert r.status_code == 200, r.text
    ligada = r.json()["id"]
    sem_loja = PricingAccount(
        user_id=manual_editor.id, name="velasco", platform="mercadolivre",
        listing_type="ml premium", segment_id=celular,
    )
    shopee_store = await _create_store(client, "shopee", account_name="loja x")
    shopee_sem_loja = PricingAccount(
        user_id=manual_editor.id, name="loja x", platform="shopee", segment_id=celular,
    )
    db.add_all([sem_loja, shopee_sem_loja])
    await db.commit()
    ids = (sem_loja.id, shopee_sem_loja.id)

    r = await client.delete(f"/api/pricing/store-info/{ml_store}/department/celular")
    assert r.status_code == 204, r.text
    r = await client.delete(f"/api/pricing/store-info/{shopee_store}/department/celular")
    assert r.status_code == 204, r.text

    db.expire_all()
    restantes = set((await db.execute(select(PricingAccount.id))).scalars())
    assert ligada not in {str(i) for i in restantes}
    assert ids[0] in restantes        # ML sem loja, mesmo nome: fica
    assert ids[1] not in restantes    # Shopee sem loja, mesmo nome: sai, como antes
