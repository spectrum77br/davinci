"""Troca do e-mail da conta pelo balão da loja em Empresas (05/10/2026).

O e-mail do balão é o de Lojas (store_info.email): trocar em Empresas tem que
mudar lá, mover o vínculo de Cadastros e nunca pegar e-mail de outra conta.
"""
import asyncio

import pytest
from sqlalchemy import select, text

import app.db as _db
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


async def seed(db, make_user, auth_as, *, com_store=True):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    company = Company(razao_social="Vortan Ltda", apelido="vortan")
    velho = Cadastro(tipo=CadastroTipo.EMAIL, codigo="fredys03")
    db.add_all([company, velho])
    await db.flush()
    info = StoreInfo(
        user_id=admin.id, platform="amazon", account_name="vortan",
        email="fredys03", phone="11951091368", server="163",
    )
    db.add(info)
    store = None
    if com_store:
        store = Store(company_id=company.id, marketplace=Marketplace.AMAZON)
        db.add(store)
        await db.flush()
        db.add(CadastroStore(cadastro_id=velho.id, store_id=store.id, alias="vortan"))
    await db.commit()
    return admin, company, info, store, velho


def body(company, email, marketplace="amazon"):
    return {"company_id": str(company.id), "marketplace": marketplace, "email": email}


async def livres(client, marketplace="amazon"):
    r = await client.get("/api/cadastros/available", params={
        "tipo": "email", "marketplace": marketplace,
    })
    assert r.status_code == 200, r.text
    return {c["codigo"] for c in r.json()}


@pytest.mark.asyncio
async def test_troca_muda_lojas_e_move_vinculo_de_cadastros(db, client, make_user, auth_as):
    _, company, info, store, velho = await seed(db, make_user, auth_as)
    novo = Cadastro(tipo=CadastroTipo.EMAIL, codigo="21leona")
    db.add(novo)
    await db.commit()
    assert "21leona" in await livres(client)
    assert "fredys03" not in await livres(client)

    r = await client.put("/api/stores/account/email", json=body(company, " 21leona "))
    assert r.status_code == 200, r.text
    assert r.json() == {
        "email": "21leona", "anterior": "fredys03",
        "em_cadastros": True, "lojas_atualizadas": 1,
    }
    await db.refresh(info)
    assert info.email == "21leona"
    # A aba Lojas lê a mesma linha.
    lojas = (await client.get("/api/pricing/store-info")).json()
    assert [s["email"] for s in lojas if s["id"] == str(info.id)] == ["21leona"]
    vinculos = (await db.execute(
        select(CadastroStore.cadastro_id).where(CadastroStore.store_id == store.id)
    )).scalars().all()
    assert vinculos == [novo.id]
    assert "fredys03" in await livres(client)
    assert "21leona" not in await livres(client)


@pytest.mark.asyncio
async def test_email_fora_de_cadastros_grava_so_na_loja(db, client, make_user, auth_as):
    _, company, info, store, _ = await seed(db, make_user, auth_as)
    r = await client.put("/api/stores/account/email", json=body(company, "21leona"))
    assert r.status_code == 200, r.text
    assert r.json()["em_cadastros"] is False
    await db.refresh(info)
    assert info.email == "21leona"
    assert (await db.execute(
        select(CadastroStore).where(CadastroStore.store_id == store.id)
    )).scalars().all() == []


@pytest.mark.asyncio
async def test_conta_so_em_lojas_sem_store(db, client, make_user, auth_as):
    _, company, info, _, _ = await seed(db, make_user, auth_as, com_store=False)
    r = await client.put("/api/stores/account/email", json=body(company, "21leona"))
    assert r.status_code == 200, r.text
    await db.refresh(info)
    assert info.email == "21leona"


@pytest.mark.asyncio
async def test_email_de_outra_conta_no_mesmo_marketplace_barra(db, client, make_user, auth_as):
    admin, company, info, store, velho = await seed(db, make_user, auth_as)
    db.add(StoreInfo(user_id=admin.id, platform="amazon", account_name="fils", email="21LEONA"))
    await db.commit()
    r = await client.put("/api/stores/account/email", json=body(company, "21leona"))
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == {"code": "email_em_uso", "conta": "fils"}
    await db.refresh(info)
    assert info.email == "fredys03"
    vinculos = (await db.execute(
        select(CadastroStore.cadastro_id).where(CadastroStore.store_id == store.id)
    )).scalars().all()
    assert vinculos == [velho.id]


@pytest.mark.asyncio
async def test_vinculo_importado_de_outra_conta_barra(db, client, make_user, auth_as):
    """É o caso do fredys03: planilha antiga diz amazon = fils."""
    _, company, info, _, _ = await seed(db, make_user, auth_as)
    db.add(Cadastro(tipo=CadastroTipo.EMAIL, codigo="23leona", raw_links={"amazon": "fils"}))
    await db.commit()
    r = await client.put("/api/stores/account/email", json=body(company, "23leona"))
    assert r.status_code == 409
    assert r.json()["detail"]["conta"] == "fils"
    await db.refresh(info)
    assert info.email == "fredys03"


@pytest.mark.asyncio
async def test_vinculo_em_cadastros_de_outra_loja_barra(db, client, make_user, auth_as):
    _, company, info, _, _ = await seed(db, make_user, auth_as)
    outra = Company(razao_social="Fils Ltda", apelido="fils")
    cad = Cadastro(tipo=CadastroTipo.EMAIL, codigo="21leona")
    db.add_all([outra, cad])
    await db.flush()
    loja = Store(company_id=outra.id, marketplace=Marketplace.AMAZON)
    db.add(loja)
    await db.flush()
    db.add(CadastroStore(cadastro_id=cad.id, store_id=loja.id, alias="fils"))
    await db.commit()
    r = await client.put("/api/stores/account/email", json=body(company, "21leona"))
    assert r.status_code == 409
    assert r.json()["detail"]["conta"] == "fils"


@pytest.mark.asyncio
async def test_mesmo_email_em_outro_marketplace_pode(db, client, make_user, auth_as):
    admin, company, info, _, _ = await seed(db, make_user, auth_as)
    db.add(StoreInfo(user_id=admin.id, platform="shopee", account_name="fils", email="21leona"))
    db.add(Cadastro(tipo=CadastroTipo.EMAIL, codigo="21leona", raw_links={"ml": "aguiar2"}))
    await db.commit()
    r = await client.put("/api/stores/account/email", json=body(company, "21leona"))
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_cadastro_desativado_barra(db, client, make_user, auth_as):
    _, company, info, _, _ = await seed(db, make_user, auth_as)
    db.add(Cadastro(tipo=CadastroTipo.EMAIL, codigo="21leona", status=CadastroStatus.EXCLUDED))
    await db.commit()
    r = await client.put("/api/stores/account/email", json=body(company, "21leona"))
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "email_desativado", "codigo": "21leona"}
    await db.refresh(info)
    assert info.email == "fredys03"


@pytest.mark.asyncio
async def test_codigo_repetido_com_um_ativo_pode(db, client, make_user, auth_as):
    """A lista de livres oferece o ativo; a troca não pode barrar pelo excluído."""
    _, company, _, store, _ = await seed(db, make_user, auth_as)
    excluido = Cadastro(tipo=CadastroTipo.EMAIL, codigo="21leona", status=CadastroStatus.EXCLUDED)
    ativo = Cadastro(tipo=CadastroTipo.EMAIL, codigo="21LEONA ")
    db.add_all([excluido, ativo])
    await db.commit()
    assert "21LEONA " in await livres(client)
    r = await client.put("/api/stores/account/email", json=body(company, "21leona"))
    assert r.status_code == 200, r.text
    vinculos = (await db.execute(
        select(CadastroStore.cadastro_id).where(CadastroStore.store_id == store.id)
    )).scalars().all()
    assert vinculos == [ativo.id]


@pytest.mark.asyncio
async def test_solta_vinculo_importado_do_email_velho_da_propria_conta(
    db, client, make_user, auth_as,
):
    _, company, _, _, velho = await seed(db, make_user, auth_as)
    velho.raw_links = {"ml": "nexus", "amazon": "Vortan"}
    await db.commit()
    r = await client.put("/api/stores/account/email", json=body(company, "21leona"))
    assert r.status_code == 200, r.text
    await db.refresh(velho)
    assert velho.raw_links == {"ml": "nexus"}
    assert "fredys03" in await livres(client)


@pytest.mark.asyncio
async def test_outro_marketplace_da_mesma_empresa_nao_muda(db, client, make_user, auth_as):
    admin, company, _, _, _ = await seed(db, make_user, auth_as)
    shopee = StoreInfo(user_id=admin.id, platform="shopee", account_name="vortan", email="fredys03")
    db.add(shopee)
    await db.commit()
    r = await client.put("/api/stores/account/email", json=body(company, "21leona"))
    assert r.status_code == 200, r.text
    await db.refresh(shopee)
    assert shopee.email == "fredys03"


@pytest.mark.asyncio
async def test_email_vazio_e_conta_inexistente(db, client, make_user, auth_as):
    _, company, info, _, _ = await seed(db, make_user, auth_as)
    r = await client.put("/api/stores/account/email", json=body(company, "   "))
    assert r.status_code == 422
    r = await client.put("/api/stores/account/email", json=body(company, "x", "magalu"))
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "store_info_not_found"
    await db.refresh(info)
    assert info.email == "fredys03"


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["empresa", "cadastro", "lojas_info"])
async def test_precisa_das_tres_permissoes(db, client, make_user, auth_as, missing):
    _, company, info, _, _ = await seed(db, make_user, auth_as)
    user = await make_user(role=UserRole.USER, permissions={
        resource: {"view": True, "edit": True}
        for resource in ("empresa", "cadastro", "lojas_info") if resource != missing
    })
    auth_as(user)
    r = await client.put("/api/stores/account/email", json=body(company, "21leona"))
    assert r.status_code == 403
    await db.refresh(info)
    assert info.email == "fredys03"


@pytest.mark.asyncio
async def test_respeita_equipe_da_aba_lojas(db, client, make_user, auth_as):
    """Quem não vê a loja em Lojas (outra equipe) também não troca aqui."""
    _, company, info, store, velho = await seed(db, make_user, auth_as)
    info.sales_team = 2
    user = await make_user(role=UserRole.USER, permissions={
        r: {"view": True, "edit": True} for r in ("empresa", "cadastro", "lojas_info")
    })
    user.sales_teams = [1]
    await db.commit()
    await db.refresh(user)
    auth_as(user)
    r = await client.put("/api/stores/account/email", json=body(company, "21leona"))
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "store_info_not_found"
    await db.refresh(info)
    assert info.email == "fredys03"
    vinculos = (await db.execute(
        select(CadastroStore.cadastro_id).where(CadastroStore.store_id == store.id)
    )).scalars().all()
    assert vinculos == [velho.id]


@pytest.mark.asyncio
async def test_outro_email_ligado_a_loja_continua(db, client, make_user, auth_as):
    """Só o vínculo do e-mail velho sai; um e-mail de recuperação fica preso."""
    _, company, _, store, velho = await seed(db, make_user, auth_as)
    recuperacao = Cadastro(tipo=CadastroTipo.EMAIL, codigo="recuperacao9")
    db.add(recuperacao)
    await db.flush()
    db.add(CadastroStore(cadastro_id=recuperacao.id, store_id=store.id, alias="vortan"))
    await db.commit()
    r = await client.put("/api/stores/account/email", json=body(company, "21leona"))
    assert r.status_code == 200, r.text
    vinculos = (await db.execute(
        select(CadastroStore.cadastro_id).where(CadastroStore.store_id == store.id)
    )).scalars().all()
    assert vinculos == [recuperacao.id]
    assert "recuperacao9" not in await livres(client)
    assert "fredys03" in await livres(client)


@pytest.mark.asyncio
async def test_ml_com_duas_linhas_solta_os_dois_emails_velhos(db, client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    company = Company(razao_social="Dream", apelido="dream 2")
    a = Cadastro(tipo=CadastroTipo.EMAIL, codigo="olda", raw_links={"ml": "dream2"})
    b = Cadastro(tipo=CadastroTipo.EMAIL, codigo="oldb", raw_links={"mercadolivre": "Dream 2"})
    db.add_all([company, a, b])
    await db.flush()
    l1 = StoreInfo(user_id=admin.id, platform="ml", account_name="dream2", email="olda")
    l2 = StoreInfo(user_id=admin.id, platform="mercadolivre", account_name="Dream 2", email="oldb")
    db.add_all([l1, l2])
    await db.commit()
    r = await client.put("/api/stores/account/email", json=body(company, "novo1", "ml"))
    assert r.status_code == 200, r.text
    assert r.json()["lojas_atualizadas"] == 2
    for row in (l1, l2, a, b):
        await db.refresh(row)
    assert (l1.email, l2.email) == ("novo1", "novo1")
    assert a.raw_links == {} and b.raw_links == {}
    assert {"olda", "oldb"} <= await livres(client, "ml")


@pytest.mark.asyncio
async def test_email_longo_volta_422(db, client, make_user, auth_as):
    _, company, info, _, _ = await seed(db, make_user, auth_as)
    r = await client.put("/api/stores/account/email", json=body(company, "a" * 300))
    assert r.status_code == 422
    await db.refresh(info)
    assert info.email == "fredys03"


async def _esperando(check, n):
    for _ in range(200):
        r = await check.execute(text("SELECT count(*) FROM pg_locks WHERE NOT granted"))
        if r.scalar() >= n:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("timeout esperando trava")


@pytest.mark.asyncio
async def test_troca_e_criacao_de_conta_no_mesmo_email_nao_travam(db, client, make_user, auth_as):
    """Mesma ordem de travas da criação de conta: sem deadlock (500)."""
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    vortan = Company(razao_social="Vortan", apelido="vortan", enabled_marketplaces=["amazon"])
    nova = Company(razao_social="Nova", apelido="nova", enabled_marketplaces=["amazon"])
    velho = Cadastro(tipo=CadastroTipo.EMAIL, codigo="fredys03")
    novo = Cadastro(tipo=CadastroTipo.EMAIL, codigo="21leona")
    fone = Cadastro(tipo=CadastroTipo.FONE, codigo="1199")
    serv = Cadastro(tipo=CadastroTipo.SERVIDOR, codigo="999")
    db.add_all([vortan, nova, velho, novo, fone, serv])
    await db.flush()
    db.add(StoreInfo(user_id=admin.id, platform="amazon", account_name="vortan", email="fredys03"))
    loja = Store(company_id=vortan.id, marketplace=Marketplace.AMAZON)
    db.add(loja)
    await db.flush()
    db.add(CadastroStore(cadastro_id=velho.id, store_id=loja.id, alias="vortan"))
    await db.commit()

    async with _db.engine.connect() as holder, _db.engine.connect() as check:
        await holder.begin()
        await holder.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                             {"k": "store-account:amazon:email:21leona"})
        troca = asyncio.create_task(client.put("/api/stores/account/email", json={
            "company_id": str(vortan.id), "marketplace": "amazon", "email": "21leona",
        }))
        await _esperando(check, 1)
        cria = asyncio.create_task(client.post("/api/stores/account", json={
            "company_id": str(nova.id), "marketplace": "amazon",
            "phone_id": str(fone.id), "email_id": str(novo.id), "server_id": str(serv.id),
        }))
        await _esperando(check, 2)
        await holder.rollback()
        troca_r, cria_r = await asyncio.wait_for(asyncio.gather(troca, cria), timeout=15)
    assert troca_r.status_code == 200, troca_r.text
    assert cria_r.status_code == 409, cria_r.text
