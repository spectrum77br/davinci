# ruff: noqa: F811 — o fixture make_user (com a loja das garantias) vem dos testes do SAC
"""O contato do comprador na aba Dados da garantia (09/11/2026): o e-mail e o
WhatsApp — o robô que lê o cadastro do contato do pedido no Bling (só onde o
Bling tem: hoje a Amazon), a escolha SAC × Bling nos Detalhes, o mascarado de
quem não vê o CPF completo e a migration 0462."""

from __future__ import annotations

import importlib.util
import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text, update

from app.models import BlingOrder, UserRole
from app.models.base import Base
from app.services import garantia_contato as contato
from tests.test_garantia_sac import _formulario
from tests.test_garantia_sac_rotas import (  # noqa: F401 — make_user é fixture
    CONSULTA,
    VE_CPF,
    _caso,
    _garantia,
    _hoje,
    make_user,
)

pytestmark = pytest.mark.asyncio

_VERSOES = Path(__file__).resolve().parent.parent / "alembic" / "versions"


# ── As regras puras ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("telefone", "esperado"),
    [
        ("(19) 98888-7777", "5519988887777"),
        ("(19) 3433-1234", "551934331234"),
        ("19988887777", "5519988887777"),
        ("+55 19 98888-7777", "5519988887777"),
        ("5519988887777", "5519988887777"),
        ("(19) 98888-7777 — https://wa.me/5519988887777", "5519988887777"),
        ("98888-7777", None),  # sem DDD
        ("(019) 98888-7777", None),
        ("(19) 9****-7777", None),  # mascarado pela plataforma
        ("(11) 99999-9999", None),  # cara de número de teste
        ("", None),
        (None, None),
    ],
)
async def test_whatsapp(telefone, esperado):
    assert contato.whatsapp(telefone) == esperado


async def test_formatos_e_mascaras():
    assert contato.telefone_legivel("5519988887777") == "(19) 98888-7777"
    assert contato.telefone_legivel("551934331234") == "(19) 3433-1234"
    assert contato.telefone_mascarado("5519988887777") == "(19) 9****-7777"
    assert contato.telefone_mascarado("551934331234") == "(19) 3***-1234"
    assert contato.email_mascarado("maria@gmail.com") == "m***@gmail.com"
    assert contato.email_valido(" Maria@Gmail.com ") == "maria@gmail.com"
    assert contato.email_valido("m***@gmail.com") is None
    assert contato.email_valido("sem arroba") is None
    assert contato.email_repassador("abc123@marketplace.amazon.com.br")
    assert not contato.email_repassador("maria@gmail.com")


# ── O robô que lê o Bling ────────────────────────────────────────────────────


class _Resposta:
    def __init__(self, dados, status=200):
        self._dados, self.status_code = dados, status

    def raise_for_status(self):
        if self.status_code >= 400:
            req = httpx.Request("GET", "https://bling.test")
            raise httpx.HTTPStatusError(
                "erro", request=req, response=httpx.Response(self.status_code, request=req)
            )

    def json(self):
        return {"data": self._dados}


class _Bling:
    """O Bling de mentira: pedido → contato. `status` por bling_id simula 404/500."""

    def __init__(self, contatos: dict[int, dict], status: dict[int, int] | None = None):
        self.contatos, self.status, self.chamadas = contatos, status or {}, []

    async def _request(self, metodo, caminho, **_):
        bid = int(caminho.rsplit("/", 1)[-1])
        self.chamadas.append(bid)
        if bid in self.status:
            return _Resposta({}, self.status[bid])
        return _Resposta({"id": bid, "contato": {"id": bid * 10} if bid in self.contatos else {}})

    async def get_contato(self, contato_id):
        return self.contatos[contato_id // 10]


async def _espelho(db, numero, bling_id):
    """O espelho do pedido (que o `_garantia` já criou) ganha o id do Bling."""
    await db.execute(
        update(BlingOrder).where(BlingOrder.numero == numero).values(bling_id=bling_id)
    )
    await db.commit()


async def _com_plataforma(db, g, plataforma):
    g.plataforma = plataforma
    await db.commit()
    return g


async def test_robo_le_o_bling_so_onde_ha_contato(db):
    amazon = await _com_plataforma(db, await _garantia(db, "700001"), "amazon")
    amazon_fixo = await _com_plataforma(db, await _garantia(db, "700002"), "amazon")
    amazon_vazio = await _com_plataforma(db, await _garantia(db, "700003"), "amazon")
    apagado = await _com_plataforma(db, await _garantia(db, "700004"), "amazon")
    fora_do_ar = await _com_plataforma(db, await _garantia(db, "700005"), "amazon")
    sem_espelho = await _com_plataforma(db, await _garantia(db, "700006"), "amazon")
    shopee = await _com_plataforma(db, await _garantia(db, "700007"), "shopee")
    ml = await _com_plataforma(db, await _garantia(db, "700008"), "ml")
    for numero, bid in (("700001", 1), ("700002", 2), ("700003", 3), ("700004", 4), ("700005", 5)):
        await _espelho(db, numero, bid)
    await _espelho(db, "700007", 7)
    await _espelho(db, "700008", 8)
    bling = _Bling(
        {
            1: {
                "email": "abc@marketplace.amazon.com.br",
                "telefone": "",
                "celular": "(19) 98888-7777",
            },
            2: {
                "email": "",
                "emailNotaFiscal": "nf@empresa.com.br",
                "telefone": "1934331234",
                "celular": "",
            },
            3: {"email": "", "telefone": "988887777", "celular": ""},
        },
        status={4: 404, 5: 500},
    )

    r = await contato.preencher(db, client=bling)
    await db.commit()
    assert r == {
        "alvo": 6,
        "lidos": 4,
        "com_telefone": 2,
        "com_email": 2,
        "sem_pedido": 1,
        "falhas": 1,
    }
    # Mercado Livre e Shopee: o Bling não tem — nem pergunta.
    assert 7 not in bling.chamadas and 8 not in bling.chamadas
    for g in (amazon, amazon_fixo, amazon_vazio, apagado, fora_do_ar, sem_espelho, shopee, ml):
        await db.refresh(g)
    assert (amazon.contato_telefone, amazon.contato_email) == (
        "5519988887777",
        "abc@marketplace.amazon.com.br",
    )
    assert (amazon_fixo.contato_telefone, amazon_fixo.contato_email) == (
        "551934331234",
        "nf@empresa.com.br",
    )
    # Sem DDD não vira WhatsApp; lido e vazio = o Bling não tem.
    assert (amazon_vazio.contato_telefone, amazon_vazio.contato_lido_em is not None) == (None, True)
    assert apagado.contato_lido_em is not None  # 404: nada a ler, não insiste
    assert fora_do_ar.contato_lido_em is None  # 500: tenta na próxima volta
    assert sem_espelho.contato_lido_em is None  # o espelho ainda não chegou
    assert shopee.contato_lido_em is None and ml.contato_lido_em is None

    # A próxima volta só tenta de novo quem ficou para trás.
    bling.chamadas.clear()
    bling.status = {}
    bling.contatos[5] = {"email": "", "telefone": "", "celular": "11 97777-6666"}
    await contato.preencher(db, client=bling)
    await db.commit()
    await db.refresh(fora_do_ar)
    assert bling.chamadas == [5] and fora_do_ar.contato_telefone == "5511977776666"


# ── Os Detalhes ──────────────────────────────────────────────────────────────


async def test_detalhe_mostra_o_contato_completo_so_para_quem_ve_o_cpf(
    client, db, make_user, auth_as
):
    g = await _com_plataforma(
        db, await _garantia(db, entrega=_hoje() - timedelta(days=10)), "amazon"
    )
    g.contato_telefone, g.contato_email = "5519988887777", "abc@marketplace.amazon.com.br"
    g.contato_lido_em = datetime.now(UTC)
    await db.commit()

    auth_as(await make_user(permissions=VE_CPF))
    c = (await client.get(f"/api/garantias/{g.id}")).json()["contato"]
    assert c == {
        "email": "abc@marketplace.amazon.com.br",
        "email_repassador": True,
        "email_fonte": "bling",
        "whatsapp": "(19) 98888-7777",
        "whatsapp_link": "https://wa.me/5519988887777",
        "whatsapp_fonte": "bling",
        "completo": True,
        "vazio": None,
    }

    auth_as(await make_user(permissions=CONSULTA))
    r = await client.get(f"/api/garantias/{g.id}")
    c = r.json()["contato"]
    assert (c["whatsapp"], c["whatsapp_link"], c["email"], c["completo"]) == (
        "(19) 9****-7777",
        None,
        "a***@marketplace.amazon.com.br",
        False,
    )
    assert "98888" not in r.text and "wa.me" not in r.text

    # A resposta de um POST (conferir a entrega) vem mascarada, como o CPF.
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.post(f"/api/garantias/{g.id}/recalcular")
    assert r.status_code == 200, r.text
    assert r.json()["contato"]["completo"] is False and "wa.me" not in r.text


async def test_o_sac_vale_mais_que_o_bling_e_os_motivos_do_vazio(client, db, make_user, auth_as):
    g = await _com_plataforma(
        db, await _garantia(db, entrega=_hoje() - timedelta(days=10)), "amazon"
    )
    g.contato_telefone, g.contato_email = "5519911112222", "abc@marketplace.amazon.com.br"
    g.contato_lido_em = datetime.now(UTC)
    await db.commit()
    await _caso(db, g, texto=_formulario(protocolo="US-26-0101"))
    auth_as(await make_user(role=UserRole.ADMIN))
    c = (await client.get(f"/api/garantias/{g.id}")).json()["contato"]
    assert (c["email"], c["email_fonte"], c["email_repassador"]) == (
        "maria@example.com",
        "sac",
        False,
    )
    assert (c["whatsapp"], c["whatsapp_fonte"]) == ("(19) 98888-7777", "sac")

    # Shopee sem SAC: a plataforma não informa.
    s = await _com_plataforma(db, await _garantia(db, "600002"), "shopee")
    assert (await client.get(f"/api/garantias/{s.id}")).json()["contato"] == {
        "email": None,
        "email_repassador": False,
        "email_fonte": None,
        "whatsapp": None,
        "whatsapp_link": None,
        "whatsapp_fonte": None,
        "completo": True,
        "vazio": "plataforma",
    }
    # Amazon que o robô ainda não leu; e a que ele leu e o Bling não tinha.
    a = await _com_plataforma(db, await _garantia(db, "600003"), "amazon")
    assert (await client.get(f"/api/garantias/{a.id}")).json()["contato"]["vazio"] == "lendo"
    a.contato_lido_em = datetime.now(UTC)
    await db.commit()
    assert (await client.get(f"/api/garantias/{a.id}")).json()["contato"]["vazio"] == "sem_cadastro"


# ── Migration ────────────────────────────────────────────────────────────────


def _carregar(nome: str):
    spec = importlib.util.spec_from_file_location(f"migration_{nome}", _VERSOES / f"{nome}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_COLUNAS = text(
    "SELECT column_name, data_type, is_nullable FROM information_schema.columns"
    " WHERE table_schema = :schema AND table_name = 'garantias' AND column_name LIKE 'contato%'"
    " ORDER BY column_name"
)


async def test_migration_0462_sobe_igual_ao_model_e_desce(db):
    mod = _carregar("0462_garantia_contato")
    assert (mod.revision, mod.down_revision) == (
        "0462_garantia_contato",
        "0461_certificacao_garantia",
    )
    for passo in (mod.upgrade, mod.downgrade):
        assert "SET LOCAL lock_timeout = '5s'" in inspect.getsource(passo)
    schema_model = Base.metadata.schema
    rascunho = f"{schema_model}_mig0462"
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    await db.execute(text(f'CREATE TABLE "{rascunho}".garantias (id serial PRIMARY KEY)'))
    await db.commit()

    def _rodar(conn, passo: str) -> None:
        ctx = MigrationContext.configure(conn, opts={"target_metadata": Base.metadata})
        with Operations.context(ctx):
            getattr(mod, passo)()

    mod.SCHEMA = rascunho
    try:
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade")
        await db.commit()
        da_migration = (await db.execute(_COLUNAS, {"schema": rascunho})).all()
        assert da_migration == (await db.execute(_COLUNAS, {"schema": schema_model})).all()
        assert [tuple(x) for x in da_migration] == [
            ("contato_email", "text", "YES"),
            ("contato_lido_em", "timestamp with time zone", "YES"),
            ("contato_telefone", "text", "YES"),
        ]
        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade")
        await db.commit()
        assert (await db.execute(_COLUNAS, {"schema": rascunho})).all() == []
    finally:
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()
