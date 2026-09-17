"""Recursos de uma conta só podem ser reutilizados em outro marketplace."""

import pytest

from app.models import (
    Cadastro,
    CadastroStatus,
    CadastroStore,
    CadastroTipo,
    Company,
    Marketplace,
    Store,
    StoreInfo,
    UserRole,
)
from app.services.cadastro_availability import available_cadastros


async def _cadastro(db, tipo, codigo, **kwargs):
    cadastro = Cadastro(tipo=tipo, codigo=codigo, **kwargs)
    db.add(cadastro)
    await db.flush()
    return cadastro


async def _store(db, marketplace):
    company = Company(razao_social="Empresa de teste", apelido="conta")
    db.add(company)
    await db.flush()
    store = Store(company_id=company.id, marketplace=marketplace)
    db.add(store)
    await db.flush()
    return store


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tipo", "field"),
    [
        (CadastroTipo.FONE, "phone"),
        (CadastroTipo.EMAIL, "email"),
    ],
)
async def test_available_checks_all_sources_only_on_selected_marketplace(
    db, client, make_user, auth_as, tipo, field
):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    stores = {mk: await _store(db, mk) for mk in (Marketplace.ML, Marketplace.SHOPEE)}
    assigned = {}
    for mk in (Marketplace.ML, Marketplace.SHOPEE):
        linked = await _cadastro(db, tipo, f"link-{mk}")
        imported = await _cadastro(db, tipo, f"import-{mk}", raw_links={mk.value: "conta"})
        in_lojas = await _cadastro(db, tipo, f"  Em-Lojas-{mk} \t")
        db.add(CadastroStore(cadastro_id=linked.id, store_id=stores[mk].id))
        db.add(
            StoreInfo(
                user_id=admin.id,
                platform=f" {mk.upper()} ",
                account_name="loja",
                **{field: f"\tEM-LOJAS-{mk.upper()}  "},
            )
        )
        assigned[mk] = {str(c.id) for c in (linked, imported, in_lojas)}
    free = await _cadastro(db, tipo, "livre")
    await db.commit()

    for target, other in (
        (Marketplace.ML, Marketplace.SHOPEE),
        (Marketplace.SHOPEE, Marketplace.ML),
    ):
        response = await client.get(
            "/api/cadastros/available", params={"tipo": tipo.value, "marketplace": target.value}
        )
        assert response.status_code == 200, response.text
        assert {row["id"] for row in response.json()} == assigned[other] | {str(free.id)}


@pytest.mark.asyncio
@pytest.mark.parametrize("fonte", ["vinculo", "raw_link", "store_info"])
async def test_servidor_ocupado_some_de_todos_os_marketplaces(
    db, client, make_user, auth_as, fonte
):
    """Servidor é perfil do AdsPower: usado no ML, não aparece na Shopee (17/09)."""
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    ocupado = await _cadastro(db, CadastroTipo.SERVIDOR, "  AdsPower-104 ")
    livre = await _cadastro(db, CadastroTipo.SERVIDOR, "adspower-105")
    if fonte == "vinculo":
        store = await _store(db, Marketplace.ML)
        db.add(CadastroStore(cadastro_id=ocupado.id, store_id=store.id))
    elif fonte == "raw_link":
        ocupado.raw_links = {" Mercado Livre ": "conta importada"}
    else:
        db.add(
            StoreInfo(
                user_id=admin.id,
                platform=" ML ",
                account_name="conta existente",
                server="\tADSPOWER-104 ",
            )
        )
    await db.commit()

    for mk in (Marketplace.ML, Marketplace.SHOPEE, Marketplace.TIKTOK):
        response = await client.get(
            "/api/cadastros/available", params={"tipo": "servidor", "marketplace": mk.value}
        )
        assert response.status_code == 200, response.text
        assert {row["id"] for row in response.json()} == {str(livre.id)}, mk


@pytest.mark.asyncio
async def test_fone_e_email_continuam_reservados_por_marketplace(db, make_user):
    """A regra global vale só pro servidor — a mesma linha atende 2 plataformas."""
    owner = await make_user(role=UserRole.ADMIN)
    for tipo, campo, codigo in (
        (CadastroTipo.FONE, "phone", "11999990000"),
        (CadastroTipo.EMAIL, "email", "conta@exemplo.com"),
    ):
        cadastro = await _cadastro(db, tipo, codigo)
        db.add(
            StoreInfo(
                user_id=owner.id,
                platform="ml",
                account_name=f"conta {tipo.value}",
                **{campo: codigo},
            )
        )
        await db.flush()
        assert await available_cadastros(db, tipo, Marketplace.ML) == []
        assert [c.id for c in await available_cadastros(db, tipo, Marketplace.SHOPEE)] == [
            cadastro.id
        ]


@pytest.mark.asyncio
async def test_available_excludes_inactive_excluded_and_blank_codes(db):
    active = await _cadastro(db, CadastroTipo.SERVIDOR, "perfil livre", raw_links={"ml": " \t"})
    await _cadastro(db, CadastroTipo.SERVIDOR, "inativo", status=CadastroStatus.INACTIVE)
    await _cadastro(db, CadastroTipo.SERVIDOR, "excluído", status=CadastroStatus.EXCLUDED)
    await _cadastro(db, CadastroTipo.SERVIDOR, "")
    await _cadastro(db, CadastroTipo.SERVIDOR, " \t\n ")
    await db.flush()

    available = await available_cadastros(db, CadastroTipo.SERVIDOR, Marketplace.ML)
    assert [cadastro.id for cadastro in available] == [active.id]


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["link", "raw_link", "store_info"])
@pytest.mark.parametrize("busy_status", [CadastroStatus.INACTIVE, CadastroStatus.EXCLUDED])
async def test_assigned_code_cannot_be_reused_through_an_active_duplicate(
    db, make_user, source, busy_status
):
    owner = await make_user(role=UserRole.ADMIN)
    old = await _cadastro(db, CadastroTipo.EMAIL, "  Conta@EXEMPLO.COM \t", status=busy_status)
    duplicate = await _cadastro(db, CadastroTipo.EMAIL, "conta@exemplo.com")
    other_type = await _cadastro(db, CadastroTipo.DOMINIO, "conta@exemplo.com")
    if source == "link":
        store = await _store(db, Marketplace.AMAZON)
        db.add(CadastroStore(cadastro_id=old.id, store_id=store.id))
    elif source == "raw_link":
        old.raw_links = {" Amazon ": "empresa importada"}
    else:
        db.add(
            StoreInfo(
                user_id=owner.id,
                platform=" AMAZON ",
                account_name="conta existente",
                email="\tCONTA@exemplo.com  ",
            )
        )
    await db.flush()

    assert await available_cadastros(db, CadastroTipo.EMAIL, Marketplace.AMAZON) == []
    assert [c.id for c in await available_cadastros(db, CadastroTipo.EMAIL, Marketplace.ML)] == [
        duplicate.id
    ]
    assert [
        c.id for c in await available_cadastros(db, CadastroTipo.DOMINIO, Marketplace.AMAZON)
    ] == [other_type.id]


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["ml", " ML ", "mercadolivre", " Mercado Livre "])
async def test_mercado_livre_store_info_aliases_reserve_the_same_resource(db, make_user, platform):
    owner = await make_user(role=UserRole.ADMIN)
    cadastro = await _cadastro(db, CadastroTipo.FONE, "  11951090424  ")
    db.add(
        StoreInfo(
            user_id=owner.id,
            platform=platform,
            account_name="conta existente",
            phone="11951090424",
        )
    )
    await db.flush()

    assert await available_cadastros(db, CadastroTipo.FONE, Marketplace.ML) == []
    assert [c.id for c in await available_cadastros(db, CadastroTipo.FONE, Marketplace.AMAZON)] == [
        cadastro.id
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("params", "error_code"),
    [
        ({"tipo": "email", "marketplace": "inexistente"}, "marketplace_invalid"),
        ({"tipo": "inexistente", "marketplace": "ml"}, "tipo_invalid"),
    ],
)
async def test_available_rejects_invalid_filters_without_database_error(
    client, make_user, auth_as, params, error_code
):
    auth_as(await make_user(role=UserRole.ADMIN))
    response = await client.get("/api/cadastros/available", params=params)
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == error_code


@pytest.mark.asyncio
async def test_available_preserves_view_permission(client, make_user, auth_as):
    auth_as(await make_user(role=UserRole.USER, permissions={}))
    response = await client.get(
        "/api/cadastros/available", params={"tipo": "email", "marketplace": "ml"}
    )
    assert response.status_code == 403
