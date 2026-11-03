"""Respostas prontas por equipe (03/11/2026).

O Eduardo: "separe por 3 equipes que separamos la mas as q ja tem la deixe pra
todos mas as novas que eles vao criar vai ter essa ideia de especifico por
equipe". Na fase de observação (`rota.SO_ADMIN`, como em produção):

- as que já existiam (a 0443 põe a coluna sem mexer nelas) são "para todos";
- quem é de equipe (londres [101], israel [102], marrocos [103]) vê as "para
  todos" + as da equipe dele, cria a da equipe dele e só mexe nas da equipe;
- o admin da lista vê todas, escolhe a equipe ou "para todos" e mexe em tudo;
  a "para todos" só ele muda ou apaga;
- quem não é de equipe (sem `sales_teams`, ou só com outro código, como o
  cairo [201]) só lê, como antes.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.config import get_settings
from app.models import AtendimentoModelo, Integration, IntegrationPlatform, User, UserRole
from app.models.pricing import StoreInfo
from app.routers import atendimento as rota
from app.security.cipher import encrypt_json
from app.services.atendimento import manual, respostas_prontas

URL = "/api/atendimento"
DONO = "dono-prontas@davinci-test.com"


@pytest.fixture(autouse=True)
def _fase_de_observacao(monkeypatch):
    monkeypatch.setattr(rota, "SO_ADMIN", True)
    monkeypatch.setattr(get_settings(), "atendimento_usuarios", DONO)


async def _pessoa(db, make_user, equipes: list[int] | None, **kw) -> User:
    u = await make_user(**kw)
    u.sales_teams = equipes
    await db.commit()
    return u


@pytest.fixture
async def gente(db, make_user):
    return {
        "dono": await make_user(email=DONO, role=UserRole.ADMIN),
        "admin_fora": await make_user(role=UserRole.ADMIN),
        "londres": await _pessoa(db, make_user, [101]),
        "israel": await _pessoa(db, make_user, [102]),
        "sem_equipe": await _pessoa(db, make_user, None),
        "cairo": await _pessoa(db, make_user, [201, 10001]),
    }


@pytest.fixture
async def antiga(db) -> AtendimentoModelo:
    """Uma resposta de antes da 0443: sem equipe = para todos."""
    m = AtendimentoModelo(titulo="Obrigado", texto="Obrigado pela compra, {comprador}!")
    db.add(m)
    await db.commit()
    return m


async def _lista(client, auth_as, user) -> dict[str, dict]:
    auth_as(user)
    r = await client.get(f"{URL}/modelos")
    assert r.status_code == 200, r.text
    return {m["titulo"]: m for m in r.json()}


async def test_quem_e_de_equipe_cria_a_da_equipe_e_ve_todos_mais_a_dele(
    client, auth_as, gente, antiga
):
    auth_as(gente["londres"])
    r = await client.post(f"{URL}/modelos", json={"titulo": "Rastreio L", "texto": "Segue."})
    assert r.status_code == 201, r.text
    assert (r.json()["equipe"], r.json()["equipe_nome"], r.json()["pode_editar"]) == (
        101,
        "Londres",
        True,
    )
    # Pedir "para todos" não vale para quem é de equipe: nasce com a dele.
    r = await client.post(
        f"{URL}/modelos", json={"titulo": "Prazo L", "texto": "2 dias.", "equipe": None}
    )
    assert r.json()["equipe"] == 101
    # Nem a de outra equipe; e o número tem de ser de uma equipe do atendimento.
    r = await client.post(f"{URL}/modelos", json={"titulo": "X", "texto": "x", "equipe": 102})
    assert (r.status_code, r.json()["detail"]["code"]) == (403, "resposta_de_outra_equipe")
    r = await client.post(f"{URL}/modelos", json={"titulo": "X", "texto": "x", "equipe": 201})
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "equipe_invalida")

    auth_as(gente["israel"])
    r = await client.post(f"{URL}/modelos", json={"titulo": "Rastreio I", "texto": "Ok."})
    assert r.json()["equipe"] == 102

    londres = await _lista(client, auth_as, gente["londres"])
    assert set(londres) == {"Obrigado", "Rastreio L", "Prazo L"}
    assert londres["Obrigado"]["equipe"] is None and londres["Obrigado"]["pode_editar"] is False
    assert londres["Rastreio L"]["pode_editar"] is True
    assert set(await _lista(client, auth_as, gente["israel"])) == {"Obrigado", "Rastreio I"}
    # Quem não é de equipe (ou só tem outro código) vê só as para todos.
    assert set(await _lista(client, auth_as, gente["sem_equipe"])) == {"Obrigado"}
    assert set(await _lista(client, auth_as, gente["cairo"])) == {"Obrigado"}
    # O admin vê todas; o da lista mexe em todas, o de fora só lê.
    dono = await _lista(client, auth_as, gente["dono"])
    assert set(dono) == {"Obrigado", "Rastreio L", "Prazo L", "Rastreio I"}
    assert all(m["pode_editar"] for m in dono.values())
    fora = await _lista(client, auth_as, gente["admin_fora"])
    assert set(fora) == set(dono) and not any(m["pode_editar"] for m in fora.values())


async def test_editar_e_apagar_so_a_propria_equipe_e_a_de_todos_so_o_admin(
    client, auth_as, gente, antiga
):
    auth_as(gente["londres"])
    minha = (await client.post(f"{URL}/modelos", json={"titulo": "L", "texto": "l"})).json()

    # A da outra equipe: nem existe para o israel.
    auth_as(gente["israel"])
    r = await client.patch(f"{URL}/modelos/{minha['id']}", json={"texto": "invadi"})
    assert (r.status_code, r.json()["detail"]["code"]) == (404, "modelo_nao_encontrado")
    r = await client.delete(f"{URL}/modelos/{minha['id']}")
    assert r.status_code == 404

    # A para todos: o londres vê, mas só o admin muda ou apaga.
    auth_as(gente["londres"])
    for r in (
        await client.patch(f"{URL}/modelos/{antiga.id}", json={"texto": "mudei"}),
        await client.patch(f"{URL}/modelos/{antiga.id}", json={"equipe": 101}),
        await client.delete(f"{URL}/modelos/{antiga.id}"),
    ):
        assert (r.status_code, r.json()["detail"]["code"]) == (403, "resposta_de_todos_so_admin")

    # A própria: edita, não passa para outra equipe nem para todos, apaga.
    r = await client.patch(f"{URL}/modelos/{minha['id']}", json={"texto": "novo", "ativo": False})
    assert (r.status_code, r.json()["texto"], r.json()["ativo"]) == (200, "novo", False)
    r = await client.patch(f"{URL}/modelos/{minha['id']}", json={"equipe": 102})
    assert (r.status_code, r.json()["detail"]["code"]) == (403, "resposta_de_outra_equipe")
    r = await client.patch(f"{URL}/modelos/{minha['id']}", json={"equipe": None})
    assert (r.status_code, r.json()["equipe"]) == (200, 101)
    assert (await client.delete(f"{URL}/modelos/{minha['id']}")).status_code == 204

    # Quem não é de equipe e o admin fora da lista: só leitura, como antes.
    for quem in ("sem_equipe", "cairo", "admin_fora"):
        auth_as(gente[quem])
        for r in (
            await client.post(f"{URL}/modelos", json={"titulo": "X", "texto": "x"}),
            await client.patch(f"{URL}/modelos/{antiga.id}", json={"texto": "x"}),
            await client.delete(f"{URL}/modelos/{antiga.id}"),
        ):
            assert (r.status_code, r.json()["detail"]["code"]) == (403, "atendimento_so_leitura")


async def test_o_admin_escolhe_a_equipe_ou_todos_e_mexe_em_qualquer_uma(
    client, auth_as, gente, antiga
):
    auth_as(gente["dono"])
    r = await client.post(f"{URL}/modelos", json={"titulo": "M", "texto": "m", "equipe": 103})
    marrocos = r.json()
    assert (r.status_code, marrocos["equipe"], marrocos["equipe_nome"]) == (201, 103, "Marrocos")
    r = await client.post(f"{URL}/modelos", json={"titulo": "T", "texto": "t"})
    assert (r.status_code, r.json()["equipe"]) == (201, None)
    r = await client.post(f"{URL}/modelos", json={"titulo": "X", "texto": "x", "equipe": 7})
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "equipe_invalida")

    r = await client.patch(f"{URL}/modelos/{antiga.id}", json={"equipe": 102, "texto": "só I"})
    assert (r.json()["equipe"], r.json()["texto"]) == (102, "só I")
    r = await client.patch(f"{URL}/modelos/{antiga.id}", json={"equipe": None})
    assert r.json()["equipe"] is None
    assert (await client.delete(f"{URL}/modelos/{marrocos['id']}")).status_code == 204


async def test_as_equipes_do_formulario(client, auth_as, gente):
    async def equipes(quem):
        auth_as(gente[quem])
        r = await client.get(f"{URL}/modelos/equipes")
        assert r.status_code == 200, r.text
        return r.json()

    dono = await equipes("dono")
    assert dono["equipes"] == [
        {"numero": 101, "nome": "Londres"},
        {"numero": 102, "nome": "Israel"},
        {"numero": 103, "nome": "Marrocos"},
    ]
    assert (dono["todas"], dono["minhas"]) == (True, [])
    assert ((await equipes("londres"))["todas"], (await equipes("londres"))["minhas"]) == (
        False,
        [101],
    )
    for quem in ("sem_equipe", "cairo", "admin_fora"):
        e = await equipes(quem)
        assert (e["todas"], e["minhas"]) == (False, []), quem


def test_a_trava_deixa_so_as_rotas_de_resposta_pronta_para_a_equipe():
    caminhos = {(m, r.path) for r in rota.router.routes for m in getattr(r, "methods", ())}
    assert rota.ROTAS_DA_EQUIPE <= caminhos
    assert {p for _, p in rota.ROTAS_DA_EQUIPE} == {
        f"{URL}/modelos",
        f"{URL}/modelos/{{modelo_id}}",
    }


async def test_o_manual_base_so_mexe_nas_de_todos(db, antiga):
    db.add(AtendimentoModelo(titulo="Da equipe", texto="só L", equipe=101))
    await db.commit()
    exportado = await manual.exportar_manual(db)
    assert [r["titulo"] for r in exportado["respostas_prontas"]] == ["Obrigado"]


async def test_equipes_da_loja_pela_ficha(db, make_user):
    """A equipe da loja da conversa (a base do agente): o FK da ficha; sem ele,
    o par nome + plataforma da ponte do e-mail."""

    async def integracao(nome: str) -> Integration:
        i = Integration(
            user_id=dono.id,
            platform=IntegrationPlatform.SHOPEE,
            name=nome,
            credentials=encrypt_json({"access_token": "t"}),
        )
        db.add(i)
        await db.commit()
        return i

    dono = await make_user()
    kia, vortan = await integracao("kia"), await integracao("vortan")
    assert await respostas_prontas.equipes_da_loja(db, None) == set()
    assert await respostas_prontas.equipes_da_loja(db, kia.id) == set()
    db.add_all(
        [
            StoreInfo(
                user_id=dono.id,
                platform="shopee",
                account_name="kia",
                integration_id=kia.id,
                sales_team=102,
            ),
            StoreInfo(user_id=dono.id, platform="shopee", account_name="vortan", sales_team=103),
        ]
    )
    await db.commit()
    assert await respostas_prontas.equipes_da_loja(db, kia.id) == {102}
    assert await respostas_prontas.equipes_da_loja(db, vortan.id) == {103}


# ── A migration 0443: as que já existem ficam para todos ─────────────────────


async def test_migration_0443_poe_a_coluna_e_as_antigas_ficam_para_todos(db, monkeypatch):
    caminho = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0443_atendimento_modelo_equipe.py"
    )
    spec = importlib.util.spec_from_file_location("m0443", caminho)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    assert mig.revision == "0443_atendimento_modelo_equipe"
    assert mig.down_revision == "0442_pricing_conferencia"
    schema = "m0443_" + uuid4().hex[:12]
    monkeypatch.setattr(mig, "SCHEMA", schema)

    def rodar(passo):
        def _f(conn):
            ctx = MigrationContext.configure(conn)
            mig.op = Operations(ctx)
            passo()

        return _f

    engine = db.bind
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.execute(
                text(
                    f'CREATE TABLE "{schema}".atendimento_modelos '
                    "(id uuid PRIMARY KEY, titulo text NOT NULL, texto text NOT NULL)"
                )
            )
            for titulo in ("Obrigado", "Rastreio"):
                await conn.execute(
                    text(f'INSERT INTO "{schema}".atendimento_modelos VALUES (:id, :t, :t)'),  # noqa: S608
                    {"id": uuid4(), "t": titulo},
                )
            await conn.run_sync(rodar(mig.upgrade))
            linhas = (
                await conn.execute(
                    text(f'SELECT titulo, equipe FROM "{schema}".atendimento_modelos ORDER BY 1')  # noqa: S608
                )
            ).all()
            assert [tuple(x) for x in linhas] == [("Obrigado", None), ("Rastreio", None)]
            indices = (
                await conn.execute(
                    text("SELECT indexname FROM pg_indexes WHERE schemaname = :s"), {"s": schema}
                )
            ).scalars()
            assert mig.INDICE in set(indices)
            await conn.run_sync(rodar(mig.downgrade))
            colunas = (
                await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = :s AND table_name = 'atendimento_modelos'"
                    ),
                    {"s": schema},
                )
            ).scalars()
            assert "equipe" not in set(colunas)
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
