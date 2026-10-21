"""O "Novo fato" do agente (Claude) e os fatos da garantia Uranyx (21/10/2026).

- POST /api/atendimento/fatos: só ADMIN cria (403 `so_admin` para quem não
  é); o texto (10 a 1000), o assunto (formato e se EXISTE no manual), o
  escopo e os campos dele, a marca dos sites e a loja (existe?) são
  conferidos, e nada é gravado quando falha;
- o fato criado nasce ATIVO, fonte `dono`, aprovado por quem criou (ou
  candidato, com `ativo=false`), aparece no GET /fatos e a busca do agente
  (`agente.fatos_loja.fatos_aplicaveis`) acha pela pergunta — o `tsv` é o
  mesmo que a aplicação calcula;
- a migration 0422: os dois fatos da garantia Uranyx aprovados pelo dono
  nascem ativos, rodar de novo não duplica, o agente acha na conversa da
  marca (só nela) e o downgrade apaga só os dois.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Text, cast, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Base, Integration, IntegrationPlatform, User, UserRole
from app.models.atendimento import AtendimentoFato
from app.routers import atendimento as rota
from app.security.cipher import encrypt_json
from app.services.atendimento.agente import fatos_loja
from app.services.atendimento.busca_texto import tsv_de

URL = "/api/atendimento/fatos"
PODE_TUDO = {"atendimento": {"view": True, "edit": True}}
TEXTO = "A garantia do aparelho Uranyx é acionada pelo app Uranyx."
_API = Path(__file__).resolve().parent.parent
_MIGRATION = _API / "alembic" / "versions" / "0422_atendimento_fatos_uranyx.py"
GARANTIA_1_ANO = (
    "Aparelhos Uranyx homologados pela Uranyx têm 1 ano de garantia: os 90 primeiros dias "
    "com a loja e os 9 meses seguintes direto com a Uranyx."
)
GARANTIA_CAMINHO = (
    "Depois dos 90 primeiros dias, a garantia do aparelho Uranyx é acionada direto com a "
    "Uranyx, pelo app Uranyx ou pelo site uranyx.com.br/sac."
)


@pytest.fixture(autouse=True)
def _permissao_fina(monkeypatch):
    """Como nos outros testes do router: a permissão fina (sem a trava da fase de observação)."""
    monkeypatch.setattr(rota, "SO_ADMIN", False)


@pytest.fixture(autouse=True)
async def _sem_fatos_soltos(db: AsyncSession):
    """Fato global/da marca não tem FK que a limpeza geral apague."""
    await db.execute(delete(AtendimentoFato))
    await db.commit()
    yield
    await db.execute(delete(AtendimentoFato))
    await db.commit()


@pytest.fixture
async def admin(make_user, auth_as) -> User:
    u = await make_user(role=UserRole.ADMIN)
    auth_as(u)
    return u


async def _fatos(db: AsyncSession) -> list[AtendimentoFato]:
    return list(
        (
            await db.execute(
                select(AtendimentoFato)
                .order_by(AtendimentoFato.id)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )


async def _aplicaveis(
    db: AsyncSession,
    *,
    plataforma: str = "shopee",
    integration_id=None,
    item_id: str | None = None,
    marca: str | None = None,
    assunto: str | None = None,
    pergunta: str | None = None,
) -> set[str]:
    """Os ids ("fato:<id>") que a busca do AGENTE devolve nesta conversa."""
    achados = await fatos_loja.fatos_aplicaveis(
        db,
        plataforma=plataforma,
        integration_id=integration_id,
        item_id=item_id,
        product_ids=[],
        skus=[],
        marca=marca,
        assunto=assunto,
        pergunta=pergunta,
        agora=datetime.now(UTC),
    )
    return {f["id"] for f in achados}


async def _loja(db: AsyncSession, dono: User, nome: str = "kia") -> Integration:
    integ = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform.SHOPEE,
        name=nome,
        credentials=encrypt_json({"access_token": "t"}),
    )
    db.add(integ)
    await db.commit()
    return integ


# ─────────────── POST /fatos ───────────────


async def test_quem_nao_e_admin_nao_cria(client, db, make_user, auth_as):
    auth_as(await make_user(permissions=PODE_TUDO))
    r = await client.post(URL, json={"texto": TEXTO})
    assert (r.status_code, r.json()["detail"]["code"]) == (403, "so_admin")
    assert await _fatos(db) == []


@pytest.mark.parametrize(
    ("corpo", "trecho"),
    [
        ({}, None),
        ({"texto": "curto"}, None),
        ({"texto": "   nove ch   "}, None),
        ({"texto": "x" * 1001}, None),
        ({"texto": TEXTO, "escopo": "produto"}, None),
        ({"texto": TEXTO, "assunto": "Não é assunto!"}, "categoria inválida"),
        ({"texto": TEXTO, "escopo": "plataforma"}, "precisa de: plataforma"),
        ({"texto": TEXTO, "escopo": "plataforma", "plataforma": "orkut"}, "plataforma"),
        ({"texto": TEXTO, "escopo": "marca"}, "precisa de: marca"),
        ({"texto": TEXTO, "escopo": "marca", "marca": "apple"}, "marca desconhecida"),
        ({"texto": TEXTO, "marca": "uranyx"}, "não usa: marca"),
        ({"texto": TEXTO, "escopo": "plataforma", "plataforma": "ml", "marca": "uranyx"}, None),
        ({"texto": TEXTO, "escopo": "loja"}, "precisa de: loja"),
        ({"texto": TEXTO, "escopo": "anuncio", "plataforma": "shopee"}, "precisa de: anúncio"),
        ({"texto": TEXTO, "escopo": "anuncio", "item_id": "123"}, "precisa de: plataforma"),
        ({"texto": TEXTO, "ativo": "talvez"}, None),
    ],
)
async def test_validacao_do_corpo(client, db, admin, corpo, trecho):
    r = await client.post(URL, json=corpo)
    assert r.status_code == 422, r.json()
    if trecho is not None:
        assert trecho in str(r.json()["detail"]), r.json()
    assert await _fatos(db) == []


async def test_assunto_fora_do_manual_e_loja_que_nao_existe(client, db, admin):
    r = await client.post(URL, json={"texto": TEXTO, "assunto": "assunto_que_nao_existe"})
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "categoria_invalida")
    r = await client.post(
        URL, json={"texto": TEXTO, "escopo": "loja", "integration_id": str(uuid4())}
    )
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "loja_nao_encontrada")
    assert await _fatos(db) == []


async def test_admin_cria_fato_ativo_da_marca_que_o_agente_acha(client, db, admin):
    r = await client.post(
        URL,
        json={
            "texto": f"  {TEXTO}  ",
            "assunto": "Garantia",
            "escopo": "marca",
            "marca": " Uranyx ",
        },
    )
    assert r.status_code == 201, r.json()
    f = r.json()
    assert (f["texto"], f["escopo"], f["marca"], f["assunto"]) == (
        TEXTO,
        "marca",
        "uranyx",
        "garantia",
    )
    assert (f["fonte"], f["ativo"], f["estado"], f["aprovado_por"]) == (
        "dono",
        True,
        "ativo",
        admin.email,
    )
    assert f["aprovado_em"] is not None
    assert f["chave"].startswith("dono_") and len(f["chave"]) == len("dono_") + 8
    assert f["plataforma"] is None and f["integration_id"] is None

    # Aparece na lista de aprovação, nos ativos, com a fonte `dono`.
    r = await client.get(URL, params={"estado": "ativo"})
    (listado,) = [x for x in r.json()["fatos"] if x["id"] == f["id"]]
    assert (listado["fonte"], listado["estado"]) == ("dono", "ativo")
    r = await client.get(URL, params={"fonte": "dono"})
    assert [x["id"] for x in r.json()["fatos"]] == [f["id"]]

    # O `tsv` saiu preenchido e é o mesmo que a aplicação calcula.
    (gravado,) = await _fatos(db)
    assert gravado.tsv is not None
    tsv = await db.scalar(
        select(cast(AtendimentoFato.tsv, Text)).where(AtendimentoFato.id == f["id"])
    )
    assert tsv == await db.scalar(select(cast(tsv_de(TEXTO), Text)))

    # A busca do agente acha pela pergunta (sem acento, outro assunto) na
    # conversa da Uranyx — e só nela.
    fato_id = f"fato:{f['id']}"
    assert fato_id in await _aplicaveis(
        db, marca="uranyx", assunto="outro", pergunta="Como aciono a garantia?"
    )
    assert fato_id in await _aplicaveis(db, marca="uranyx", assunto="garantia")
    assert fato_id not in await _aplicaveis(
        db, marca="charlots", assunto="outro", pergunta="Como aciono a garantia?"
    )
    assert fato_id not in await _aplicaveis(db, marca=None, pergunta="garantia")


async def test_global_e_plataforma_e_o_recorte_da_marca(client, db, admin):
    r = await client.post(URL, json={"texto": "Todos os produtos seguem com nota fiscal."})
    assert r.status_code == 201
    glob = r.json()
    assert (glob["escopo"], glob["assunto"], glob["fonte"]) == ("global", None, "dono")

    r = await client.post(
        URL,
        json={
            "texto": "Na Shopee o envio sai em até dois dias úteis.",
            "escopo": "plataforma",
            "plataforma": "shopee",
            "assunto": "",
        },
    )
    assert r.status_code == 201
    plat = r.json()
    assert (plat["escopo"], plat["plataforma"], plat["assunto"]) == ("plataforma", "shopee", None)

    # A marca só da Shopee (o recorte da plataforma).
    r = await client.post(
        URL,
        json={
            "texto": "A air fryer Uranyx vendida na Shopee é de 220 V.",
            "escopo": "marca",
            "marca": "uranyx",
            "plataforma": "shopee",
        },
    )
    assert r.status_code == 201
    marca = r.json()

    ids = {k: f"fato:{v['id']}" for k, v in {"g": glob, "p": plat, "m": marca}.items()}
    assert await _aplicaveis(db, plataforma="shopee", marca="uranyx") == set(ids.values())
    assert await _aplicaveis(db, plataforma="ml", marca="uranyx") == {ids["g"]}


async def test_ativo_false_nasce_candidato(client, db, admin):
    r = await client.post(URL, json={"texto": TEXTO, "ativo": False})
    assert r.status_code == 201
    f = r.json()
    assert (f["ativo"], f["estado"], f["aprovado_por"], f["aprovado_em"], f["fonte"]) == (
        False,
        "candidato",
        None,
        None,
        "dono",
    )
    r = await client.get(URL, params={"estado": "candidato"})
    assert [x["id"] for x in r.json()["fatos"]] == [f["id"]]
    assert await _aplicaveis(db, pergunta="garantia app") == set()
    # O admin aprova depois, pela mesma lista.
    r = await client.post(f"{URL}/{f['id']}/aprovar", json={})
    assert (r.status_code, r.json()["estado"]) == (200, "ativo")
    assert await _aplicaveis(db, pergunta="garantia app") == {f"fato:{f['id']}"}


async def test_loja_e_anuncio(client, db, admin):
    integ = await _loja(db, admin)
    r = await client.post(
        URL,
        json={
            "texto": "Esta loja envia pela transportadora.",
            "escopo": "loja",
            "integration_id": str(integ.id),
            "plataforma": "shopee",
        },
    )
    assert r.status_code == 201, r.json()
    loja = r.json()
    assert (loja["escopo"], loja["integration_id"], loja["loja"]) == ("loja", str(integ.id), "kia")
    r = await client.post(
        URL,
        json={
            "texto": "O envio deste produto é feito de São Paulo.",
            "escopo": "anuncio",
            "plataforma": "shopee",
            "item_id": " 22299473628 ",
        },
    )
    assert r.status_code == 201, r.json()
    anuncio = r.json()
    assert (anuncio["escopo"], anuncio["item_id"]) == ("anuncio", "22299473628")
    achados = await _aplicaveis(db, integration_id=integ.id, item_id="22299473628")
    assert achados == {f"fato:{loja['id']}", f"fato:{anuncio['id']}"}


# ─────────────── migration 0422 ───────────────


def _carregar_migration():
    spec = importlib.util.spec_from_file_location("migration_0422_fatos_uranyx", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rodar(conn, passo: str, mod) -> None:
    ctx = MigrationContext.configure(conn, opts={"target_metadata": Base.metadata})
    with Operations.context(ctx):
        getattr(mod, passo)()


def test_migration_0422_esta_na_arvore_com_uma_ponta():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    mod = _carregar_migration()
    assert mod.revision == "0422_atendimento_fatos_uranyx"
    assert mod.down_revision == "0421_devolucao_estoque_status"
    cfg = Config(str(_API / "alembic.ini"))
    cfg.set_main_option("script_location", str(_API / "alembic"))
    scripts = ScriptDirectory.from_config(cfg)
    assert len(scripts.get_heads()) == 1, scripts.get_heads()
    assert scripts.get_revision(mod.revision).down_revision == "0421_devolucao_estoque_status"


async def test_migration_0422_semeia_a_garantia_uranyx_e_o_downgrade_tira_so_ela(
    db: AsyncSession,
):
    mod = _carregar_migration()
    mod.SCHEMA = Base.metadata.schema
    # Um fato que não é da 0422: o downgrade não pode levá-lo junto.
    outro = AtendimentoFato(
        texto="O Uranyx S5 tem NFC.",
        escopo="marca",
        marca="uranyx",
        fonte="manual",
        ativo=True,
        aprovado_por="regra aprovada no código",
    )
    db.add(outro)
    await db.commit()

    conn = await db.connection()
    await conn.run_sync(_rodar, "upgrade", mod)
    await db.commit()

    linhas = {f.chave: f for f in await _fatos(db) if f.chave in mod.CHAVES}
    assert set(linhas) == {"uranyx_garantia_1_ano", "uranyx_garantia_caminho"}
    assert linhas["uranyx_garantia_1_ano"].texto == GARANTIA_1_ANO
    assert linhas["uranyx_garantia_caminho"].texto == GARANTIA_CAMINHO
    for chave, f in linhas.items():
        assert (f.escopo, f.marca, f.assunto, f.fonte, f.ativo) == (
            "marca",
            "uranyx",
            "garantia",
            "dono",
            True,
        ), chave
        assert f.aprovado_por == "Eduardo (chat 21/10/2026)", chave
        assert f.aprovado_em is not None, chave
        assert f.plataforma is None and f.integration_id is None, chave
        # O `tsv` da semente é o mesmo que a aplicação calcula para o texto.
        tsv = await db.scalar(
            select(cast(AtendimentoFato.tsv, Text)).where(AtendimentoFato.id == f.id)
        )
        assert tsv == await db.scalar(select(cast(tsv_de(f.texto), Text))), chave

    # O agente acha os dois na conversa da Uranyx (pelo assunto e pela
    # pergunta), e nenhum na da Charlots.
    ids = {f"fato:{f.id}" for f in linhas.values()}
    assert ids <= await _aplicaveis(db, marca="uranyx", assunto="garantia")
    assert ids <= await _aplicaveis(
        db, marca="uranyx", assunto="outro", pergunta="quanto tempo de garantia tem o celular?"
    )
    assert not ids & await _aplicaveis(db, marca="charlots", assunto="garantia")

    # Idempotente: rodar de novo não duplica nem muda.
    conn = await db.connection()
    await conn.run_sync(_rodar, "upgrade", mod)
    await db.commit()
    assert await db.scalar(select(func.count()).select_from(AtendimentoFato)) == 3

    # O downgrade tira só as duas chaves.
    conn = await db.connection()
    await conn.run_sync(_rodar, "downgrade", mod)
    await db.commit()
    assert [f.id for f in await _fatos(db)] == [outro.id]
