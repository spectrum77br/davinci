"""Alertas por equipe (M-19, 23/11/2026, migration 0505).

- A cerca é do SERVIDOR: o administrador vê ADM, todas as equipes e o pessoal
  dele; quem é da equipe 101 não vê ADM nem a 102; o pessoal é só da pessoa;
  quem não tem equipe vê as equipes (a regra das outras telas), nunca ADM.
- O sino conta só o que a pessoa vê, e o lido é por pessoa.
- Filtros (Equipe, Plataforma, Contas, Buscar, Layout) e paginação no servidor.
- A loja que muda de equipe leva os alertas.
- `emit_alert` classifica pelo tipo e acha a loja pelo vínculo.
- A migration 0505 converte os antigos e o downgrade desfaz.
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from httpx import AsyncClient
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Alert,
    AlertSeverity,
    AlertType,
    Base,
    Integration,
    IntegrationPlatform,
    Product,
    ProductLink,
    StoreInfo,
    User,
    UserRole,
)
from app.security.cipher import encrypt_json
from app.services.alerts import emit_alert

pytestmark = pytest.mark.asyncio

_MIGRATION = (
    Path(__file__).resolve().parent.parent / "alembic" / "versions" / "0505_alertas_por_equipe.py"
)


async def _usuario(make_user, db: AsyncSession, *, equipes=None, admin=False) -> User:
    u = await make_user(role=UserRole.ADMIN if admin else UserRole.USER)
    if equipes is not None:
        u.sales_teams = equipes
        await db.commit()
        await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def cenario(db: AsyncSession, make_user):
    """Duas lojas: "loja londres" (ML, equipe 101) e "loja israel" (Shopee,
    102). Alertas: um ADM de todos, um ADM dirigido ao admin2, um de cada
    equipe, uma tarefa da londres e uma do admin."""
    admin = await _usuario(make_user, db, admin=True)
    admin2 = await _usuario(make_user, db, admin=True)
    londres = await _usuario(make_user, db, equipes=[101])
    israel = await _usuario(make_user, db, equipes=[102])
    livre = await _usuario(make_user, db)

    def integracao(nome: str, plataforma: IntegrationPlatform) -> Integration:
        return Integration(
            user_id=admin.id,
            platform=plataforma,
            name=nome,
            credentials=encrypt_json({"access_token": "x"}),
        )

    i101 = integracao("loja londres", IntegrationPlatform.ML)
    i102 = integracao("loja israel", IntegrationPlatform.SHOPEE)
    db.add_all([i101, i102])
    await db.flush()
    loja101 = StoreInfo(
        user_id=admin.id,
        platform="ml",
        account_name="loja londres",
        sales_team=101,
        commercial_team=1,
        integration_id=i101.id,
    )
    loja102 = StoreInfo(
        user_id=admin.id,
        platform="shopee",
        account_name="loja israel",
        sales_team=102,
        commercial_team=2,
        integration_id=i102.id,
    )
    db.add_all([loja101, loja102])
    await db.commit()

    async def alerta(**kw) -> Alert:
        a = await emit_alert(db, **kw)
        await db.commit()
        assert a is not None
        return a

    adm = await alerta(
        type=AlertType.GENERIC,
        title="WhatsApp: número restrito pela Meta",
        dedupe_key="zap:restrito:1",
        escopo="adm",
    )
    dirigido = await alerta(
        type=AlertType.GENERIC,
        title="M-19 pronta para aprovar",
        user_id=admin2.id,
        escopo="adm",
        dirigido=True,
        dedupe_key="mudanca:1:pronta:0:0",
    )
    eq101 = await alerta(
        type=AlertType.SYNC_FAILURE,
        severity=AlertSeverity.ERROR,
        title="Falha sync — ml: dg003.pi+a003.pi+a004.pi",
        user_id=israel.id,  # quem rodou o sync não muda quem vê
        escopo="equipe",
        integration_id=i101.id,
        dedupe_key="sync_failure:link101",
    )
    eq102 = await alerta(
        type=AlertType.LISTING_BANNED,
        title="Anúncio banido — shopee: mala-x",
        escopo="equipe",
        integration_id=i102.id,
        dedupe_key="listing_banned:link102",
    )
    tarefa_londres = await alerta(
        type=AlertType.TAREFA_ATRIBUIDA,
        title="Nova tarefa atribuída a você (londres)",
        user_id=londres.id,
    )
    tarefa_admin = await alerta(
        type=AlertType.TAREFA_ATRIBUIDA,
        title="Nova tarefa atribuída a você (admin)",
        user_id=admin.id,
    )
    return SimpleNamespace(
        admin=admin,
        admin2=admin2,
        londres=londres,
        israel=israel,
        livre=livre,
        i101=i101,
        i102=i102,
        loja101=loja101,
        adm=adm,
        dirigido=dirigido,
        eq101=eq101,
        eq102=eq102,
        tarefa_londres=tarefa_londres,
        tarefa_admin=tarefa_admin,
    )


async def _titulos(client: AsyncClient, **params) -> set[str]:
    r = await client.get("/api/alerts", params={"limit": 200, **params})
    assert r.status_code == 200, r.text
    return {i["title"] for i in r.json()["items"]}


def _t(*alertas: Alert) -> set[str]:
    return {a.title for a in alertas}


# ================================================================ a cerca


async def test_admin_ve_adm_todas_as_equipes_e_o_pessoal_dele(client, auth_as, cenario):
    c = cenario
    auth_as(c.admin)
    assert await _titulos(client) == _t(c.adm, c.eq101, c.eq102, c.tarefa_admin)
    # O ADM dirigido é só do admin2; o pessoal dos outros não aparece.
    auth_as(c.admin2)
    assert await _titulos(client) == _t(c.adm, c.dirigido, c.eq101, c.eq102)


async def test_equipe_101_nao_ve_adm_nem_a_102(client, auth_as, cenario):
    c = cenario
    auth_as(c.londres)
    assert await _titulos(client) == _t(c.eq101, c.tarefa_londres)
    auth_as(c.israel)
    assert await _titulos(client) == _t(c.eq102)
    # Pedir a outra equipe pelo filtro não fura a cerca.
    auth_as(c.londres)
    assert await _titulos(client, equipes="102") == set()
    assert await _titulos(client, equipes="adm") == set()
    # Nem marcar lido o que não vê.
    r = await client.post(f"/api/alerts/{c.adm.id}/read")
    assert r.status_code == 404
    r = await client.post(f"/api/alerts/{c.eq102.id}/read")
    assert r.status_code == 404


async def test_sem_equipe_ve_as_equipes_e_nunca_adm(client, auth_as, cenario):
    c = cenario
    auth_as(c.livre)
    assert await _titulos(client) == _t(c.eq101, c.eq102)


async def test_sino_conta_so_o_que_a_pessoa_ve_e_o_lido_e_por_pessoa(client, auth_as, cenario):
    c = cenario

    async def sino(user: User) -> int:
        auth_as(user)
        r = await client.get("/api/alerts/unread-count")
        assert r.status_code == 200
        return r.json()["unread"]

    assert await sino(c.admin) == 4
    assert await sino(c.londres) == 2
    assert await sino(c.israel) == 1
    assert await sino(c.livre) == 2

    auth_as(c.londres)
    r = await client.post(f"/api/alerts/{c.eq101.id}/read")
    assert r.status_code == 200 and r.json()["read_at"]
    assert await sino(c.londres) == 1
    # O mesmo alerta continua não lido para o administrador.
    assert await sino(c.admin) == 4
    auth_as(c.admin)
    r = await client.get("/api/alerts", params={"equipes": "101"})
    assert [i["read_at"] for i in r.json()["items"]] == [None]

    # "Marcar todos lidos" só mexe no que a pessoa vê.
    auth_as(c.londres)
    r = await client.post("/api/alerts/read-all")
    assert r.json()["updated"] == 1
    assert await sino(c.londres) == 0
    assert await sino(c.israel) == 1
    assert await sino(c.admin) == 4

    # Com filtro, marca só o que a lista mostra.
    auth_as(c.admin)
    r = await client.post("/api/alerts/read-all", params={"equipes": "102"})
    assert r.json()["updated"] == 1
    assert await sino(c.admin) == 3


async def test_painel_inicial_usa_o_mesmo_numero_do_sino(client, auth_as, cenario):
    auth_as(cenario.londres)
    r = await client.get("/api/dashboard")
    assert r.status_code == 200, r.text
    assert r.json()["kpis"]["alerts_unread"] == 2


# ================================================== filtros e paginação


async def test_filtros_valem_para_todos_os_alertas(client, auth_as, cenario):
    c = cenario
    auth_as(c.admin)
    assert await _titulos(client, equipes="101") == _t(c.eq101)
    assert await _titulos(client, equipes=["adm", "pessoal"]) == _t(c.adm, c.tarefa_admin)
    assert await _titulos(client, equipes="adm,102") == _t(c.adm, c.eq102)
    # Lista vazia (nenhuma caixa marcada) = nada.
    assert await _titulos(client, equipes="") == set()
    assert await _titulos(client, plataformas="shopee") == _t(c.eq102)
    assert await _titulos(client, contas=str(c.i101.id)) == _t(c.eq101)
    # Buscar por SKU (no título), pela conta e pelo texto do aviso.
    assert await _titulos(client, q="a003.pi") == _t(c.eq101)
    assert await _titulos(client, q="ISRAEL") == _t(c.eq102)
    assert await _titulos(client, q="restrito") == _t(c.adm)
    assert await _titulos(client, q="100%") == set()

    auth_as(c.admin)
    await client.post(f"/api/alerts/{c.adm.id}/read")
    assert await _titulos(client, leitura="lidos") == _t(c.adm)
    assert await _titulos(client, leitura="nao_lidos") == _t(c.eq101, c.eq102, c.tarefa_admin)
    r = await client.get("/api/alerts", params={"leitura": "lidos"})
    body = r.json()
    # "N não lidos · N total" é sem o Layout; a paginação usa `encontrados`.
    assert (body["total"], body["unread"], body["encontrados"]) == (4, 3, 1)

    # Cada linha leva o que a tabela mostra.
    r = await client.get("/api/alerts", params={"equipes": "101"})
    (item,) = r.json()["items"]
    assert item["escopo"] == "equipe"
    assert item["equipe"] == 101
    assert item["plataforma"] == "ml"
    assert item["conta"] == "loja londres"
    assert item["tipo"] == "Falha sync"
    r = await client.get("/api/alerts", params={"equipes": "adm"})
    (item,) = r.json()["items"]
    assert (item["escopo"], item["equipe"], item["plataforma"], item["tipo"]) == (
        "adm",
        None,
        None,
        "WhatsApp",
    )


async def test_paginacao_no_servidor(client, auth_as, db: AsyncSession, cenario):
    c = cenario
    agora = datetime.now(UTC)
    for n in range(7):
        db.add(
            Alert(
                escopo="adm",
                type=AlertType.LOW_STOCK,
                severity=AlertSeverity.WARNING,
                title=f"Estoque baixo: sku-{n}",
                created_at=agora - timedelta(days=1, minutes=n),
            )
        )
    await db.commit()
    auth_as(c.admin)
    vistos: list[str] = []
    for offset in (0, 5, 10):
        r = await client.get(
            "/api/alerts", params={"q": "Estoque baixo", "limit": 5, "offset": offset}
        )
        body = r.json()
        assert body["encontrados"] == 7
        vistos += [i["title"] for i in body["items"]]
    assert vistos == [f"Estoque baixo: sku-{n}" for n in range(7)]
    assert {i["tipo"] for i in body["items"]} <= {"Estoque baixo"}


async def test_opcoes_so_o_que_a_pessoa_pode_ver(client, auth_as, cenario):
    c = cenario
    auth_as(c.admin)
    r = await client.get("/api/alerts/opcoes")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["equipes"] == ["adm", "101", "102", "103", "pessoal"]
    assert body["plataformas"] == ["ml", "shopee"]
    assert [(x["nome"], x["equipe"]) for x in body["contas"]] == [
        ("loja israel", 102),
        ("loja londres", 101),
    ]
    auth_as(c.londres)
    body = (await client.get("/api/alerts/opcoes")).json()
    assert body["equipes"] == ["101", "pessoal"]
    assert body["plataformas"] == ["ml"]
    assert [x["nome"] for x in body["contas"]] == ["loja londres"]
    auth_as(c.livre)
    body = (await client.get("/api/alerts/opcoes")).json()
    assert body["equipes"] == ["101", "102", "103", "pessoal"]


async def test_loja_que_muda_de_equipe_leva_os_alertas(client, auth_as, db: AsyncSession, cenario):
    c = cenario
    await db.execute(
        update(StoreInfo)
        .where(StoreInfo.id == c.loja101.id)
        .values(sales_team=102, commercial_team=2)
    )
    await db.commit()
    auth_as(c.londres)
    assert await _titulos(client) == _t(c.tarefa_londres)
    auth_as(c.israel)
    assert await _titulos(client) == _t(c.eq101, c.eq102)
    auth_as(c.admin)
    r = await client.get("/api/alerts", params={"q": "dg003"})
    assert [i["equipe"] for i in r.json()["items"]] == [102]


# ============================================================ emit_alert


async def test_emit_alert_classifica_e_acha_a_loja(db: AsyncSession, cenario, make_user):
    c = cenario
    produto = Product(user_id=c.admin.id, sku="mala-x", name="Mala X")
    db.add(produto)
    await db.flush()
    vinculo = ProductLink(
        user_id=c.admin.id,
        product_id=produto.id,
        integration_id=c.i102.id,
        platform=IntegrationPlatform.SHOPEE,
        external_id="123",
    )
    db.add(vinculo)
    await db.commit()

    # Pelo tipo + link_id: equipe da loja do vínculo, sem destinatário.
    a = await emit_alert(
        db,
        user_id=c.londres.id,
        type=AlertType.REQUIRES_REVIEW,
        title="Revisão necessária — shopee: mala-x",
        payload={"link_id": str(vinculo.id)},
        dedupe_key=f"requires_review:{vinculo.id}",
    )
    assert (a.escopo, a.user_id, a.integration_id) == ("equipe", None, c.i102.id)
    assert (a.equipe, a.plataforma, a.conta) == (102, "shopee", "loja israel")
    # Aviso sem destinatário é um só por chave, quem quer que dispare.
    again = await emit_alert(
        db,
        user_id=c.israel.id,
        type=AlertType.REQUIRES_REVIEW,
        title="Revisão necessária — shopee: mala-x",
        payload={"link_id": str(vinculo.id)},
        dedupe_key=f"requires_review:{vinculo.id}",
    )
    assert again is None

    # Sync que falhou sem anúncio, estoque baixo, genérico: ADM de todos.
    for tipo in (AlertType.SYNC_FAILURE, AlertType.LOW_STOCK, AlertType.GENERIC):
        a = await emit_alert(db, user_id=c.londres.id, type=tipo, title=f"x {tipo.value}")
        assert (a.escopo, a.user_id) == ("adm", None), tipo
    # Vínculo que não existe mais: sem loja, fica para o administrador.
    a = await emit_alert(
        db,
        type=AlertType.SYNC_FAILURE,
        title="Falha sync — vínculo apagado",
        payload={"link_id": str(uuid.uuid4())},
    )
    assert a.escopo == "adm"
    # ADM dirigido a quem não é administrador não some: vai para todos.
    a = await emit_alert(
        db, user_id=c.londres.id, type=AlertType.GENERIC, title="y", escopo="adm", dirigido=True
    )
    assert (a.escopo, a.user_id) == ("adm", None)
    a = await emit_alert(
        db, user_id=c.admin.id, type=AlertType.GENERIC, title="z", escopo="adm", dirigido=True
    )
    assert (a.escopo, a.user_id) == ("adm", c.admin.id)
    # Tarefa: pessoal.
    a = await emit_alert(db, user_id=c.israel.id, type=AlertType.TAREFA_ATRIBUIDA, title="t")
    assert (a.escopo, a.user_id) == ("pessoal", c.israel.id)
    with pytest.raises(ValueError):
        await emit_alert(db, type=AlertType.GENERIC, title="sem dono", escopo="pessoal")
    with pytest.raises(ValueError):
        await emit_alert(db, type=AlertType.GENERIC, title="?", escopo="todo mundo")
    await db.commit()


async def test_constraints_do_modelo(db: AsyncSession, cenario):
    c = cenario
    for valores in (
        {"escopo": "equipe", "user_id": c.londres.id},
        {"escopo": "pessoal", "user_id": None},
        {"escopo": "outro", "user_id": None},
    ):
        db.add(Alert(type=AlertType.GENERIC, title="x", **valores))
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()


# ============================================================ migration 0505


def _carregar():
    spec = importlib.util.spec_from_file_location("migration_0505", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _colunas(db: AsyncSession, schema: str, tabela: str, nomes=None) -> list[tuple]:
    rows = (
        await db.execute(
            text(
                "SELECT column_name, data_type, is_nullable, column_default"
                " FROM information_schema.columns"
                " WHERE table_schema = :s AND table_name = :t ORDER BY column_name"
            ),
            {"s": schema, "t": tabela},
        )
    ).all()
    return [tuple(r) for r in rows if nomes is None or r[0] in nomes]


async def _constraints(db: AsyncSession, schema: str, tabela: str, prefixo: str = "") -> list:
    rows = (
        await db.execute(
            text(
                "SELECT co.conname, pg_get_constraintdef(co.oid) FROM pg_constraint co"
                " JOIN pg_class cl ON cl.oid = co.conrelid"
                " JOIN pg_namespace n ON n.oid = cl.relnamespace"
                " WHERE n.nspname = :s AND cl.relname = :t ORDER BY co.conname"
            ),
            {"s": schema, "t": tabela},
        )
    ).all()
    return [(r[0], r[1].replace(f"{schema}.", "")) for r in rows if r[0].startswith(prefixo)]


async def _indices(db: AsyncSession, schema: str, tabela: str, nomes: set[str]) -> list:
    rows = (
        await db.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes"
                " WHERE schemaname = :s AND tablename = :t ORDER BY indexname"
            ),
            {"s": schema, "t": tabela},
        )
    ).all()
    return [(r[0], r[1].replace(f"{schema}.", "")) for r in rows if r[0] in nomes]


NOVAS = {"escopo", "equipe", "plataforma", "integration_id", "conta", "duplicado_de", "user_id"}
INDICES = {"ix_alerts_dedupe_compartilhado", "ix_alerts_escopo_created", "ix_alerts_integration_id"}


async def test_migration_0505_converte_e_o_downgrade_desfaz(db: AsyncSession):
    mod = _carregar()
    assert (mod.revision, mod.down_revision) == (
        "0505_alertas_por_equipe",
        "0504_conciliacao_conferido_esperando",
    )
    schema_model = Base.metadata.schema
    r = f"{schema_model}_mig0505"
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{r}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{r}"'))
    # Só o que a 0505 toca, com o `alerts` como está na produção antes dela.
    for ddl in (
        "CREATE TABLE {r}.users (id uuid PRIMARY KEY, role text NOT NULL)",
        "CREATE TABLE {r}.integrations (id uuid PRIMARY KEY, name text, platform text)",
        "CREATE TABLE {r}.product_links (id uuid PRIMARY KEY, integration_id uuid, platform text)",
        "CREATE TABLE {r}.store_info (id uuid PRIMARY KEY, integration_id uuid, sales_team int)",
        "CREATE TABLE {r}.pricing_push_confirmacao (push_key text PRIMARY KEY,"
        " integration_id uuid)",
        "CREATE TABLE {r}.product_links_audit (id bigserial PRIMARY KEY,"
        " deleted_at timestamptz NOT NULL DEFAULT now(), link_id uuid, integration_id uuid,"
        " platform text)",
        "CREATE TABLE {r}.alerts (id uuid PRIMARY KEY DEFAULT gen_random_uuid(),"
        " user_id uuid NOT NULL CONSTRAINT fk_alerts_user_id_users"
        " REFERENCES {r}.users(id) ON DELETE CASCADE,"
        " type text NOT NULL, severity text NOT NULL DEFAULT 'info', title text NOT NULL,"
        " message text, dedupe_key text, payload jsonb NOT NULL DEFAULT '{{}}'::jsonb,"
        " read_at timestamptz, created_at timestamptz NOT NULL DEFAULT now())",
        "CREATE UNIQUE INDEX ix_alerts_user_dedupe ON {r}.alerts (user_id, dedupe_key)"
        " WHERE dedupe_key IS NOT NULL",
    ):
        await db.execute(text(ddl.format(r=f'"{r}"')))
    adm_a, adm_b, londres, dono = (uuid.uuid4() for _ in range(4))
    i1, link1, link_apagado = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    for uid, papel in ((adm_a, "admin"), (adm_b, "admin"), (londres, "user"), (dono, "user")):
        await db.execute(text(f'INSERT INTO "{r}".users VALUES (:i, :p)'), {"i": uid, "p": papel})
    await db.execute(
        text(f"INSERT INTO \"{r}\".integrations VALUES (:i, 'loja londres', 'ml')"), {"i": i1}
    )
    await db.execute(
        text(f"INSERT INTO \"{r}\".product_links VALUES (:l, :i, 'ml')"), {"l": link1, "i": i1}
    )
    await db.execute(
        text(f'INSERT INTO "{r}".store_info VALUES (gen_random_uuid(), :i, 101)'), {"i": i1}
    )
    # O vínculo apagado fica na trilha com a integração dele.
    await db.execute(
        text(
            f'INSERT INTO "{r}".product_links_audit (link_id, integration_id, platform)'
            " VALUES (:l, :i, 'ml')"
        ),
        {"l": link_apagado, "i": i1},
    )
    await db.execute(
        text(f"INSERT INTO \"{r}\".pricing_push_confirmacao VALUES ('envio-1', :i)"), {"i": i1}
    )
    base = datetime(2026, 11, 20, 12, tzinfo=UTC)
    ids: dict[str, uuid.UUID] = {}

    async def antigo(nome, user, tipo, chave=None, payload="{}", lido=False, minutos=0):
        ids[nome] = uuid.uuid4()
        await db.execute(
            text(
                f'INSERT INTO "{r}".alerts (id, user_id, type, title, dedupe_key, payload,'
                " read_at, created_at) VALUES (:id, :u, :t, :ti, :k, CAST(:p AS jsonb), :lido,"
                " :c)"
            ),
            {
                "id": ids[nome],
                "u": user,
                "t": tipo,
                "ti": nome,
                "k": chave,
                "p": payload,
                "lido": base if lido else None,
                "c": base + timedelta(minutes=minutos),
            },
        )

    link = f'{{"link_id": "{link1}"}}'
    await antigo("revisao", londres, "requires_review", f"requires_review:{link1}", link, True)
    await antigo(
        "revisao_copia", dono, "requires_review", f"requires_review:{link1}", link, minutos=5
    )
    await antigo(
        "falha_sem_vinculo",
        londres,
        "sync_failure",
        "sync_failure:x",
        f'{{"link_id": "{uuid.uuid4()}"}}',
    )
    await antigo(
        "banido_vinculo_apagado",
        dono,
        "listing_banned",
        f"listing_banned:{link_apagado}",
        f'{{"link_id": "{link_apagado}"}}',
    )
    await antigo("tarefa", londres, "tarefa_atribuida")
    await antigo("zap_a", adm_a, "generic", "zap:restrito:1")
    await antigo("zap_b", adm_b, "generic", "zap:restrito:1")
    await antigo("bling", dono, "generic", "webhook_bling_sig_fail:2026112012")
    await antigo(
        "preco", adm_a, "generic", "pricing_confirmacao:a:p:214", '{"push_key": "envio-1"}'
    )
    await antigo("devolucao", londres, "generic", "devolucao_exclusao_chamado:9")
    await db.commit()

    limites: dict[str, str] = {}

    def _rodar(conn, passo: str) -> None:
        ctx = MigrationContext.configure(conn, opts={"target_metadata": Base.metadata})
        with Operations.context(ctx):
            getattr(mod, passo)()
        # Revisão M-19/M-20: a 0505 mexe em tabela quente — não pode esperar
        # trava para sempre (e parar o sino de todos atrás dela).
        limites[passo] = conn.exec_driver_sql("SHOW lock_timeout").scalar()

    mod.SCHEMA = r
    try:
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade")
        await db.commit()
        assert limites["upgrade"] == "5s"

        # A estrutura é a do modelo.
        assert await _colunas(db, r, "alerta_leituras") == await _colunas(
            db, schema_model, "alerta_leituras"
        )
        assert await _constraints(db, r, "alerta_leituras") == await _constraints(
            db, schema_model, "alerta_leituras"
        )
        assert await _colunas(db, r, "alerts", NOVAS) == await _colunas(
            db, schema_model, "alerts", NOVAS
        )
        for prefixo in ("ck_alerts", "fk_alerts_integration"):
            assert await _constraints(db, r, "alerts", prefixo) == await _constraints(
                db, schema_model, "alerts", prefixo
            ), prefixo
        assert await _indices(db, r, "alerts", INDICES) == await _indices(
            db, schema_model, "alerts", INDICES
        )

        linhas = {
            row.title: row
            for row in (
                await db.execute(
                    text(
                        "SELECT title, escopo, user_id, integration_id, equipe, plataforma,"
                        f' conta, duplicado_de, payload FROM "{r}".alerts'
                    )
                )
            ).all()
        }
        rv = linhas["revisao"]
        assert (rv.escopo, rv.user_id, rv.integration_id) == ("equipe", None, i1)
        assert (rv.equipe, rv.plataforma, rv.conta) == (101, "ml", "loja londres")
        assert rv.payload["_m19_user_id"] == str(londres)
        assert rv.duplicado_de is None
        assert linhas["revisao_copia"].duplicado_de == ids["revisao"]
        assert (linhas["falha_sem_vinculo"].escopo, linhas["falha_sem_vinculo"].user_id) == (
            "adm",
            None,
        )
        # Vínculo apagado: a integração vem da trilha `product_links_audit`.
        ba = linhas["banido_vinculo_apagado"]
        assert (ba.escopo, ba.user_id, ba.integration_id, ba.equipe) == ("equipe", None, i1, 101)
        assert (linhas["tarefa"].escopo, linhas["tarefa"].user_id) == ("pessoal", londres)
        assert (linhas["devolucao"].escopo, linhas["devolucao"].user_id) == ("pessoal", londres)
        # A cópia por administrador continua dirigida a ele (não vira repetida).
        assert (linhas["zap_a"].escopo, linhas["zap_a"].user_id) == ("adm", adm_a)
        assert (linhas["zap_b"].user_id, linhas["zap_b"].duplicado_de) == (adm_b, None)
        # ADM de quem não é administrador: para todos os administradores.
        assert (linhas["bling"].escopo, linhas["bling"].user_id) == ("adm", None)
        assert (linhas["preco"].escopo, linhas["preco"].integration_id) == ("equipe", i1)
        leituras = (
            await db.execute(text(f'SELECT user_id, alert_id FROM "{r}".alerta_leituras'))
        ).all()
        assert set(leituras) == {(londres, ids["revisao"])}

        # O que a cerca nova exige já vale.
        for sql in (
            f'INSERT INTO "{r}".alerts (type, title, escopo, user_id) VALUES'
            f" ('generic', 'x', 'equipe', '{londres}')",
            f"INSERT INTO \"{r}\".alerts (type, title, escopo) VALUES ('generic', 'x', 'pessoal')",
            f'INSERT INTO "{r}".alerts (type, title, escopo, dedupe_key) VALUES'
            f" ('requires_review', 'x', 'equipe', 'requires_review:{link1}')",
        ):
            with pytest.raises(IntegrityError):
                await db.execute(text(sql))
            await db.rollback()

        # Depois da migration: o dono de antes lê a cópia e chega um alerta novo.
        await db.execute(
            text(f'INSERT INTO "{r}".alerta_leituras (user_id, alert_id) VALUES (:u, :a)'),
            {"u": dono, "a": ids["revisao_copia"]},
        )
        await db.execute(
            text(
                f'INSERT INTO "{r}".alerts (type, title, escopo, dedupe_key) VALUES'
                " ('generic', 'novo', 'adm', 'zap:novo')"
            )
        )
        await db.commit()

        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade")
        await db.commit()
        assert limites["downgrade"] == "5s"
        tabelas = (
            (
                await db.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables WHERE table_schema = :s"
                    ),
                    {"s": r},
                )
            )
            .scalars()
            .all()
        )
        assert "alerta_leituras" not in tabelas
        colunas = {c[0]: c for c in await _colunas(db, r, "alerts")}
        assert not (NOVAS - {"user_id"}) & set(colunas)
        assert colunas["user_id"][2] == "NO"
        depois = {
            row.title: row
            for row in (
                await db.execute(text(f'SELECT title, user_id, read_at, payload FROM "{r}".alerts'))
            ).all()
        }
        assert "novo" not in depois
        assert set(depois) == {
            "revisao",
            "revisao_copia",
            "falha_sem_vinculo",
            "banido_vinculo_apagado",
            "tarefa",
            "zap_a",
            "zap_b",
            "bling",
            "preco",
            "devolucao",
        }
        assert depois["revisao"].user_id == londres
        assert depois["revisao_copia"].user_id == dono
        assert depois["bling"].user_id == dono
        assert depois["revisao_copia"].read_at is not None
        assert depois["revisao"].read_at == base
        assert all("_m19_user_id" not in row.payload for row in depois.values())
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{r}" CASCADE'))
        await db.commit()


async def test_admin_le_alertas_de_quem_nao_e_mais_admin_sem_quebrar(
    client, auth_as, db: AsyncSession, cenario
):
    """Um ADM dirigido cujo administrador deixou de ser: some para ele (não é
    mais ADM) e ninguém mais o vê — o mesmo que acontecia com o aviso pessoal."""
    c = cenario
    c.admin2.role = UserRole.USER
    await db.commit()
    auth_as(c.admin2)
    assert c.dirigido.title not in await _titulos(client)
    assert (await db.execute(select(Alert).where(Alert.id == c.dirigido.id))).scalar_one()
