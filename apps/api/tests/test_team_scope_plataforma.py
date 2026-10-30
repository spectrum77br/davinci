"""Escopo por equipe pela PLATAFORMA + nome da conta (30/10/2026).

Na separação Londres (101) / Israel (102) / Marrocos (103) o nome da conta
sozinho vazava entre equipes: "inova", "barbosa", "mega" e "eron" são lojas de
equipes diferentes em plataformas diferentes, e a Shopee inova (103) aparecia
na Logística de quem é do ML inova (102). Cobre:

  - Logística: a plataforma na coluna ("Mercado Livre" + "inova");
  - Reembolso: a plataforma na frente da conta ("Shopee Inova", "ML Jlas 2")
    ou na coluna; e o aviso de "já lançado", que segue a mesma régua;
  - a linha sem plataforma nenhuma: só o nome que é exclusivo da equipe;
  - o SQL (`clausula_conta`) e o Python (`conta_no_escopo`) dando o mesmo;
  - a integração com o nome da plataforma na frente ("carrefour poofy" da
    loja "poofy") e a loja "mercadolivre" × integração "ml", que ficavam fora
    do escopo da equipe (Atendimento, Integrações);
  - a integração que o FK (`store_info.integration_id`) já dá a uma loja de
    OUTRA equipe não entra pelo par nem pela ponte (duas "inova" do ML);
  - a ponte do cadastro de Empresas (`stores`: nº do Bling → integração) para
    a loja sem o FK cujo nome não casa com o da integração.

Admin e quem não tem equipe (os do estoque) continuam vendo tudo.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.team_scope import (
    clausula_conta,
    conta_no_escopo,
    plataforma_canonica,
    resolve_team_scope,
)
from app.models import (
    BlingOrder,
    Company,
    Integration,
    IntegrationPlatform,
    Logistica,
    Marketplace,
    Refund,
    Store,
    StoreInfo,
    StoreStatus,
    User,
    UserRole,
    UserStatus,
)
from app.routers.refunds import _reembolsos_ja_lancados

PERM = {
    "integracoes": {"view": True, "edit": True},
    "reembolso": {"view": True, "edit": True},
    "logistica": {"view": True, "edit": True},
}


def _mk_user(role: UserRole, teams: list[int] | None, tags: list[str] | None = None) -> User:
    email = f"plat-{uuid.uuid4().hex[:6]}@davinci-test.com"
    return User(
        open_id=f"email:{email}",
        email=email,
        role=role,
        status=UserStatus.ACTIVE,
        permissions=PERM,
        sales_teams=teams,
        stock_tags=tags,
    )


async def _como(db: AsyncSession, auth_as, teams: list[int] | None, **kw) -> User:
    u = _mk_user(kw.pop("role", UserRole.USER), teams, **kw)
    db.add(u)
    await db.commit()
    auth_as(u)
    return u


@pytest_asyncio.fixture
async def lojas(db: AsyncSession) -> User:
    """O desenho de produção: o mesmo nome em plataformas e equipes diferentes."""
    dono = _mk_user(UserRole.ADMIN, None)
    db.add(dono)
    await db.flush()
    for platform, nome, equipe, loja in (
        ("shopee", "inova", 103, "b-shp-inova"),
        ("ml", "inova", 102, "b-ml-inova"),
        ("ml", "jlas2", 102, "b-ml-jlas2"),
        ("ml", "zorvex", 101, "b-ml-zorvex"),
    ):
        db.add(
            StoreInfo(
                user_id=dono.id,
                platform=platform,
                account_name=nome,
                bling_store_id=loja,
                sales_team=equipe,
            )
        )
    await db.commit()
    return dono


async def _pedidos_logistica(client: AsyncClient) -> set[str]:
    r = await client.get("/api/logistica")
    assert r.status_code == 200, r.text
    return {i["pedido_bling"] for i in r.json()}


async def _pedidos_reembolso(client: AsyncClient) -> set[str]:
    r = await client.get("/api/refunds")
    assert r.status_code == 200, r.text
    return {i["pedido_bling"] for i in r.json()["items"]}


def test_plataforma_canonica_das_grafias_de_cada_tabela():
    # Logística grava o rótulo; Reembolso a chave; a store_info tem as duas do ML.
    assert plataforma_canonica("Mercado Livre") == "ml"
    assert plataforma_canonica("mercadolivre") == "ml"
    assert plataforma_canonica(" ML ") == "ml"
    assert plataforma_canonica("TikTok") == "tiktok"
    assert plataforma_canonica("TikTok Shop") == "tiktok"
    assert plataforma_canonica("Magazine Luiza") == "magalu"
    assert plataforma_canonica(IntegrationPlatform.CARREFOUR) == "carrefour"
    assert plataforma_canonica(None) == ""


@pytest.mark.asyncio
async def test_logistica_mesmo_nome_em_outra_plataforma_nao_vaza(
    db: AsyncSession,
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
    lojas: User,
):
    """O caso que motivou: Shopee inova é da 103 e ML inova é da 102. Pelo
    nome sozinho a 102 via a linha da Shopee (e a 103 a do ML)."""
    db.add_all(
        [
            BlingOrder(numero="shp-inova-1", loja="b-shp-inova"),
            BlingOrder(numero="ml-inova-1", loja="b-ml-inova"),
            Logistica(pedido_bling="shp-inova-1", plataforma="Shopee", conta="inova"),
            Logistica(pedido_bling="ml-inova-1", plataforma="Mercado Livre", conta="inova"),
            # Sem pedido no espelho: só o ramo da conta decide.
            Logistica(pedido_bling="so-conta-shp", plataforma="Shopee", conta="inova"),
            Logistica(pedido_bling="so-conta-ml", plataforma="Mercado Livre", conta="inova"),
        ]
    )
    await db.commit()
    todas = {"shp-inova-1", "ml-inova-1", "so-conta-shp", "so-conta-ml"}

    await _como(db, auth_as, [102])
    assert await _pedidos_logistica(client) == {"ml-inova-1", "so-conta-ml"}

    await _como(db, auth_as, [103])
    assert await _pedidos_logistica(client) == {"shp-inova-1", "so-conta-shp"}

    # Quem tem as duas equipes vê as duas lojas.
    await _como(db, auth_as, [102, 103])
    assert await _pedidos_logistica(client) == todas

    # Admin, e o operador do estoque (sem equipe, só tags): tudo, como antes.
    await _como(db, auth_as, None, role=UserRole.ADMIN)
    assert await _pedidos_logistica(client) == todas
    await _como(db, auth_as, None, tags=["ci", "mala"])
    assert await _pedidos_logistica(client) == todas


@pytest.mark.asyncio
async def test_reembolso_casa_pela_plataforma_na_frente_ou_na_coluna(
    db: AsyncSession,
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
    lojas: User,
):
    """O Reembolso grava "Shopee Inova" (plataforma na frente, coluna vazia) ou
    a coluna `plataforma`; o cadastro tem "inova" e "jlas2". Nenhum pedido está
    no espelho do Bling: quem decide é só a conta."""
    db.add_all(
        [
            Refund(pedido_bling="r-shp-prefixo", conta="Shopee Inova"),
            Refund(pedido_bling="r-ml-prefixo", conta="ML Inova", plataforma="ml"),
            Refund(pedido_bling="r-ml-separador", conta="Mercado Livre - Inova"),
            Refund(pedido_bling="r-shp-coluna", conta="inova", plataforma="shopee"),
            # "ML Jlas 2" no Reembolso é a loja "jlas2" do cadastro.
            Refund(pedido_bling="r-jlas2", conta="ML Jlas 2"),
            # Sem plataforma: "inova" é de duas equipes (ninguém adivinha);
            # "zorvex" só existe na 101.
            Refund(pedido_bling="r-inova-sem-plataforma", conta="inova"),
            Refund(pedido_bling="r-zorvex-sem-plataforma", conta="zorvex"),
        ]
    )
    await db.commit()

    await _como(db, auth_as, [102])
    assert await _pedidos_reembolso(client) == {"r-ml-prefixo", "r-ml-separador", "r-jlas2"}

    await _como(db, auth_as, [103])
    assert await _pedidos_reembolso(client) == {"r-shp-prefixo", "r-shp-coluna"}

    await _como(db, auth_as, [101])
    assert await _pedidos_reembolso(client) == {"r-zorvex-sem-plataforma"}

    await _como(db, auth_as, None)
    assert len(await _pedidos_reembolso(client)) == 7


@pytest.mark.asyncio
async def test_aviso_de_ja_lancado_segue_a_mesma_regua(
    db: AsyncSession,
    lojas: User,
):
    """O aviso de "já lançado" só detalha (valor, quem lançou) o que a pessoa
    veria na lista; a Shopee inova não pode sair detalhada para a 102."""
    db.add(
        Refund(
            pedido_bling="r-aviso",
            conta="Shopee Inova",
            reembolso=50.0,
            created_by=lojas.id,
        )
    )
    await db.commit()

    async def _aviso(teams: list[int]):
        u = _mk_user(UserRole.USER, teams)
        db.add(u)
        await db.commit()
        out = await _reembolsos_ja_lancados(db, "r-aviso", [], bling_numero=None, user=u)
        assert out["reembolsos_existentes"] == 1
        return out["reembolsos_do_pedido"][0]

    da_102 = await _aviso([102])
    assert da_102.de_outra_equipe is True
    assert da_102.conta is None and da_102.reembolso is None

    da_103 = await _aviso([103])
    assert da_103.de_outra_equipe is False
    assert da_103.conta == "Shopee Inova" and da_103.reembolso == 50.0


@pytest.mark.asyncio
async def test_sql_e_python_dao_o_mesmo(db: AsyncSession, lojas: User):
    """`clausula_conta` (a lista) e `conta_no_escopo` (o aviso) são a mesma
    régua: espaço, caixa, acento, separador, coluna vazia, conta vazia."""
    casos = [
        ("Shopee", "inova"),
        ("  SHOPEE ", " Inova "),
        ("Mercado Livre", "inova"),
        ("mercadolivre", "Ínova"),
        ("ml", "ML Inova"),
        ("Tik Tok", "inova"),
        (None, "Shopee - Inova"),
        (None, "Shopee Inova"),
        (None, "ML Jlas 2"),
        ("", "inova"),
        (None, "zorvex"),
        ("Amazon", "zorvex"),
        (None, "Loja 206081922"),
        ("ml", ""),
        ("Magazine Luiza", "inova"),
    ]
    for i, (plataforma, conta) in enumerate(casos):
        db.add(Refund(pedido_bling=f"m{i}", plataforma=plataforma, conta=conta))
        db.add(Logistica(pedido_bling=f"m{i}", plataforma=plataforma, conta=conta))
    db.add(Logistica(pedido_bling="m-nulo", plataforma=None, conta=None))
    await db.commit()

    viu_algo = set()
    for teams in ([101], [102], [103], [102, 103]):
        scope = await resolve_team_scope(db, _mk_user(UserRole.USER, teams))
        for modelo in (Refund, Logistica):
            linhas = (await db.execute(select(modelo))).scalars().all()
            no_sql = set(
                (
                    await db.execute(
                        select(modelo.pedido_bling).where(
                            clausula_conta(scope, modelo.plataforma, modelo.conta)
                        )
                    )
                ).scalars()
            )
            no_python = {
                c.pedido_bling for c in linhas if conta_no_escopo(scope, c.plataforma, c.conta)
            }
            assert no_sql == no_python, (teams, modelo.__name__)
            viu_algo |= no_sql
    # A régua casa de fato (não é o vazio dos dois lados que bate).
    assert {"m0", "m1", "m2", "m3", "m4", "m6", "m8", "m10"} <= viu_algo
    assert "m12" not in viu_algo and "m13" not in viu_algo and "m-nulo" not in viu_algo


@pytest.mark.asyncio
async def test_resolver_chaves_por_plataforma_e_nome_exclusivo(db: AsyncSession, lojas: User):
    scope = await resolve_team_scope(db, _mk_user(UserRole.USER, [102]))
    assert scope.platform_accounts == {("ml", "inova"), ("ml", "jlas2")}
    # "inova" também é de loja da 103: sem plataforma, não vale.
    assert scope.unshared_account_keys == {"jlas2"}
    # O nome sozinho continua montado (compatibilidade), só não filtra linha.
    assert scope.account_names == {"inova", "jlas2"}

    for sem_equipe in (_mk_user(UserRole.USER, None), _mk_user(UserRole.ADMIN, [102])):
        livre = await resolve_team_scope(db, sem_equipe)
        assert livre.unrestricted is True
        assert clausula_conta(livre, Logistica.plataforma, Logistica.conta) is None
        assert conta_no_escopo(livre, "Shopee", "inova") is True


@pytest.mark.asyncio
async def test_integracao_com_a_plataforma_no_nome_entra_no_escopo(
    db: AsyncSession,
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    """As integrações "carrefour poofy" / "Carrefour VR" são das lojas "poofy" /
    "VR" da Carrefour (sem o FK preenchido) e ficavam fora do Atendimento da
    equipe. A loja "mercadolivre" casa com a integração "ml". A Shopee poofy
    de outra equipe não leva a Carrefour junto."""
    dono = _mk_user(UserRole.ADMIN, None)
    db.add(dono)
    await db.flush()

    def _integ(platform: IntegrationPlatform, name: str) -> Integration:
        i = Integration(user_id=dono.id, platform=platform, name=name, credentials=b"x")
        db.add(i)
        return i

    poofy = _integ(IntegrationPlatform.CARREFOUR, "carrefour poofy")
    vr = _integ(IntegrationPlatform.CARREFOUR, "Carrefour VR")
    makisa = _integ(IntegrationPlatform.ML, "Makisa Brand")
    shopee_poofy = _integ(IntegrationPlatform.SHOPEE, "poofy")
    await db.flush()
    for platform, nome, equipe in (
        ("carrefour", "poofy", 103),
        ("carrefour", "VR", 103),
        # Netshoes não existe nas integrações: não pode quebrar a consulta.
        ("netshoes", "poofy", 103),
        ("shopee", "poofy", 102),
        ("mercadolivre", "makisa brand", 101),
    ):
        db.add(
            StoreInfo(
                user_id=dono.id,
                platform=platform,
                account_name=nome,
                integration_id=None,
                sales_team=equipe,
            )
        )
    await db.commit()

    s103 = await resolve_team_scope(db, _mk_user(UserRole.USER, [103]))
    assert {poofy.id, vr.id} <= s103.integration_ids
    assert shopee_poofy.id not in s103.integration_ids

    s102 = await resolve_team_scope(db, _mk_user(UserRole.USER, [102]))
    assert s102.integration_ids == {shopee_poofy.id}

    s101 = await resolve_team_scope(db, _mk_user(UserRole.USER, [101]))
    assert s101.integration_ids == {makisa.id}

    # E na tela: a lista de Integrações da 103 traz as duas da Carrefour.
    await _como(db, auth_as, [103])
    r = await client.get("/api/integrations")
    assert r.status_code == 200, r.text
    assert {i["id"] for i in r.json()} == {str(poofy.id), str(vr.id)}


async def _integracao(db: AsyncSession, dono: User, platform: IntegrationPlatform, name: str):
    i = Integration(user_id=dono.id, platform=platform, name=name, credentials=b"x")
    db.add(i)
    await db.flush()
    return i


async def _integracoes_na_tela(client: AsyncClient) -> set[str]:
    r = await client.get("/api/integrations")
    assert r.status_code == 200, r.text
    return {i["id"] for i in r.json()}


@pytest.mark.asyncio
async def test_integracao_no_fk_de_loja_de_outra_equipe_nao_entra_pelo_par(
    db: AsyncSession,
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    """Duas "inova" do ML em equipes diferentes (revisão de 30/10): a da
    Israel (102) tem o FK da integração B; a da Londres (101) não tem FK. O
    par (ml, inova) da Londres casava com B, e a conta da Israel entrava nas
    Integrações/Atendimento da Londres. A integração que o FK já dá a uma
    loja de fora não entra pelo par — e o par continua valendo para a "Inova"
    do ML que nenhuma loja tem no FK."""
    dono = _mk_user(UserRole.ADMIN, None)
    db.add(dono)
    await db.flush()
    b = await _integracao(db, dono, IntegrationPlatform.ML, "inova")
    sem_fk = await _integracao(db, dono, IntegrationPlatform.ML, "Inova")
    db.add_all(
        [
            StoreInfo(
                user_id=dono.id, platform="mercadolivre", account_name="inova", sales_team=101
            ),
            StoreInfo(
                user_id=dono.id,
                platform="mercadolivre",
                account_name="inova",
                sales_team=102,
                integration_id=b.id,
            ),
        ]
    )
    await db.commit()

    londres = await resolve_team_scope(db, _mk_user(UserRole.USER, [101]))
    assert b.id not in londres.integration_ids
    assert londres.integration_ids == {sem_fk.id}
    israel = await resolve_team_scope(db, _mk_user(UserRole.USER, [102]))
    assert israel.integration_ids == {b.id}
    # Quem tem as duas equipes: as duas lojas são dele, nada é "de fora".
    ambas = await resolve_team_scope(db, _mk_user(UserRole.USER, [101, 102]))
    assert ambas.integration_ids == {b.id, sem_fk.id}

    # Na tela de Integrações, a mesma coisa.
    await _como(db, auth_as, [101])
    assert await _integracoes_na_tela(client) == {str(sem_fk.id)}
    await _como(db, auth_as, [102])
    assert await _integracoes_na_tela(client) == {str(b.id)}


async def _no_cadastro_de_empresas(
    db: AsyncSession, bling: int, integracao: Integration, marketplace: Marketplace
) -> None:
    """A loja do Bling → a integração, no cadastro de Empresas (`stores`). Uma
    empresa por loja (a trava é uma loja por marketplace em cada empresa)."""
    empresa = Company(razao_social=f"Empresa {bling} LTDA", apelido=f"empresa-{bling}")
    db.add(empresa)
    await db.flush()
    db.add(
        Store(
            company_id=empresa.id,
            marketplace=marketplace,
            status=StoreStatus.ACTIVE,
            integration_id=integracao.id,
            bling_store_id=bling,
        )
    )
    await db.flush()


@pytest.mark.asyncio
async def test_ponte_do_cadastro_de_empresas_pelo_numero_do_bling(
    db: AsyncSession,
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    """A loja sem o FK cujo nome não casa com o da integração (30/10: a
    shopee "atlas" é a integração "Jlas"; a "fiore" do ML é a " Kia") entra
    no escopo pela ponte do cadastro de Empresas: o nº do Bling da loja →
    `stores.bling_store_id` → `stores.integration_id`. Só com a plataforma
    batendo (a loja "mercadolivre" × a integração "ml" bate) e nunca a
    integração que o FK dá a uma loja de outra equipe."""
    dono = _mk_user(UserRole.ADMIN, None)
    db.add(dono)
    await db.flush()
    jlas = await _integracao(db, dono, IntegrationPlatform.SHOPEE, "Jlas")
    kia = await _integracao(db, dono, IntegrationPlatform.ML, " Kia")
    # Do ML, mas o cadastro a liga ao nº do Bling de uma loja da Shopee.
    pulse_ml = await _integracao(db, dono, IntegrationPlatform.ML, "Pulse")
    # Da Shopee "mega" da Londres pelo FK; o cadastro a liga ao nº de uma loja da Israel.
    mega = await _integracao(db, dono, IntegrationPlatform.SHOPEE, "Mega")
    for platform, nome, equipe, bling, integracao in (
        ("shopee", "atlas", 102, "205468332", None),
        ("mercadolivre", "fiore", 102, "205468334", None),
        ("shopee", "pulse", 102, "205468333", None),
        ("shopee", "nova", 102, "205468335", None),
        ("shopee", "mega", 101, "205468390", mega.id),
        ("shopee", "vortan", 101, "205468391", None),
    ):
        db.add(
            StoreInfo(
                user_id=dono.id,
                platform=platform,
                account_name=nome,
                sales_team=equipe,
                bling_store_id=bling,
                integration_id=integracao,
            )
        )
    await _no_cadastro_de_empresas(db, 205468332, jlas, Marketplace.SHOPEE)
    await _no_cadastro_de_empresas(db, 205468334, kia, Marketplace.ML)
    await _no_cadastro_de_empresas(db, 205468333, pulse_ml, Marketplace.ML)
    await _no_cadastro_de_empresas(db, 205468335, mega, Marketplace.SHOPEE)
    await db.commit()

    israel = await resolve_team_scope(db, _mk_user(UserRole.USER, [102]))
    # Jlas (Shopee × shopee) e Kia (ml × mercadolivre) pela ponte; a "Pulse"
    # do ML não serve à loja da Shopee; a Mega é da loja da Londres (FK).
    assert israel.integration_ids == {jlas.id, kia.id}
    # O FK das lojas não muda (a ponte só lê).
    fks = set((await db.execute(select(StoreInfo.integration_id))).scalars())
    assert fks == {None, mega.id}

    londres = await resolve_team_scope(db, _mk_user(UserRole.USER, [101]))
    assert londres.integration_ids == {mega.id}

    # Com as duas equipes, a Mega deixa de ser "de fora" (e já vem pelo FK).
    ambas = await resolve_team_scope(db, _mk_user(UserRole.USER, [101, 102]))
    assert ambas.integration_ids == {jlas.id, kia.id, mega.id}

    await _como(db, auth_as, [102])
    assert await _integracoes_na_tela(client) == {str(jlas.id), str(kia.id)}
    await _como(db, auth_as, [101])
    assert await _integracoes_na_tela(client) == {str(mega.id)}
