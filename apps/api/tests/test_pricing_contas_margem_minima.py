"""Aba Contas: Margem mínima % da conta (M-85) — migration 0519.

- M-85: um valor por conta (0 a 100, 2 casas, vazio pode). Só o ADM vê e grava:
  quem não é ADM recebe null (lista, grade, resposta de uma conta) e o PATCH com
  ela é recusado. No par do ML vale nas duas linhas; o Catálogo mostra a da base.
Contas FABRICADAS no schema de teste.
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
import uuid
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.historico import nomes
from app.models import (
    Base,
    PricingAccount,
    PricingPlatform,
    Segment,
    UserRole,
)

pytestmark = pytest.mark.asyncio

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "0519_contas_margem_minima.py"
)

PERM_EDITA_CONTAS = {
    "tabela_precos": {"view": True, "edit": False, "delete": False},
    "tabela_precos_contas": {"view": True, "edit": True, "delete": True},
}
CLASSICO, PREMIUM = "ml classico", "ml premium"
D = Decimal


# =========================================================== histórico


def test_historico_tem_o_nome_da_margem_minima():
    assert nomes.nome_campo("margem_minima_pct") == "Margem mínima (%)"


# =========================================================== cenário


@pytest_asyncio.fixture
async def cena(db: AsyncSession, make_user) -> SimpleNamespace:
    """Celular com 5 categorias ativas; Mala com 4 ativas e a 5ª ("Queima de
    estoque") desativada — a marg 5 da Mala não aparece na aba Contas."""
    admin = await make_user(role=UserRole.ADMIN)
    vendedor = await make_user(role=UserRole.USER, permissions=PERM_EDITA_CONTAS)
    raizes: dict[str, Segment] = {}
    folhas: dict[str, list[Segment]] = {}
    for ordem, (slug, nomes_folhas) in enumerate(
        (
            ("celular", ["Acessórios", "Diversos", "Regular", "Robusto", "Apple"]),
            ("mala", ["Acessórios", '12"', '18" e 20"', '24" acima', "Queima de estoque"]),
        )
    ):
        raiz = Segment(name=slug.title(), slug=slug, sort_order=ordem)
        db.add(raiz)
        await db.flush()
        folhas[slug] = []
        for n, nome in enumerate(nomes_folhas):
            folha = Segment(
                name=nome,
                slug=f"{slug}-{n + 1}",
                sort_order=n,
                parent_id=raiz.id,
                active=not (slug == "mala" and n == 4),
            )
            db.add(folha)
            folhas[slug].append(folha)
        raizes[slug] = raiz
    await db.commit()
    return SimpleNamespace(admin=admin, vendedor=vendedor, raizes=raizes, folhas=folhas)


async def _conta(
    db: AsyncSession, cena, nome: str, plataforma=PricingPlatform.SHOPEE, dept="celular", **kw
) -> PricingAccount:
    conta = PricingAccount(
        user_id=cena.admin.id, name=nome, platform=plataforma, segment_id=cena.raizes[dept].id, **kw
    )
    db.add(conta)
    await db.commit()
    await db.refresh(conta)
    return conta


async def _ler(db: AsyncSession, conta_id) -> PricingAccount:
    return (
        await db.execute(
            select(PricingAccount)
            .where(PricingAccount.id == conta_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def _par(db: AsyncSession, par_id) -> dict[str, PricingAccount]:
    linhas = (
        (
            await db.execute(
                select(PricingAccount)
                .where(PricingAccount.par_id == par_id)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    return {linha.listing_type: linha for linha in linhas}


def _margens(*valores: str) -> dict[str, Decimal]:
    return {f"margin{n}": D(v) for n, v in enumerate(valores, start=1)}


AGUIAR = _margens("0.549", "0.356", "0.356", "0.356")


# =========================================================== M-85 na API


async def test_margem_minima_2_casas_de_0_a_100_e_banco_segura(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cena
):
    conta = await _conta(db, cena, "aguiar", commission=D("0.14"), **AGUIAR)
    url = f"/api/pricing/accounts/{conta.id}"
    auth_as(cena.admin)
    for valor in (101, -1, "abc"):
        r = await client.patch(url, json={"margem_minima_pct": valor})
        assert r.status_code == 422, (valor, r.text)
    r = await client.patch(url, json={"margem_minima_pct": 24.995})
    assert r.status_code == 200, r.text
    assert D(r.json()["margem_minima_pct"]) == D("25.00")
    linha = await _ler(db, conta.id)
    linha.margem_minima_pct = D("100.01")
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


async def test_so_o_admin_ve_e_grava_a_margem_minima(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cena
):
    conta = await _conta(db, cena, "aguiar", commission=D("0.14"), **AGUIAR)
    url = f"/api/pricing/accounts/{conta.id}"
    auth_as(cena.admin)
    assert (await client.patch(url, json={"margem_minima_pct": 25})).status_code == 200

    auth_as(cena.vendedor)
    # Não vem em lugar nenhum.
    r = await client.get("/api/pricing/accounts", params={"department": "celular"})
    assert r.status_code == 200, r.text
    (vista,) = r.json()
    assert vista["margem_minima_pct"] is None
    r = await client.get("/api/pricing/grid", params={"department": "celular"})
    assert r.status_code == 200, r.text
    assert [a["margem_minima_pct"] for a in r.json()["accounts"]] == [None]
    r = await client.patch(url, json={"observation": "ok"})
    assert r.status_code == 200, r.text
    assert r.json()["margem_minima_pct"] is None
    # Não grava (nem apaga): o pedido inteiro é recusado.
    for corpo in ({"margem_minima_pct": 30}, {"margem_minima_pct": None, "observation": "x"}):
        r = await client.patch(url, json=corpo)
        assert r.status_code == 403, r.text
        assert "margem_minima_pct" in r.json()["detail"]["campos"]
    r = await client.post(
        "/api/pricing/accounts",
        json={
            "name": "nova",
            "platform": "shopee",
            "department": "celular",
            "margem_minima_pct": 9,
        },
    )
    assert r.status_code == 403, r.text
    linha = await _ler(db, conta.id)
    assert (linha.margem_minima_pct, linha.observation) == (D("25.00"), "ok")

    # O ADM recebe.
    auth_as(cena.admin)
    r = await client.get("/api/pricing/accounts", params={"department": "celular"})
    assert D(r.json()[0]["margem_minima_pct"]) == D("25")


async def test_par_do_ml_grava_nas_duas_e_o_catalogo_segue_a_base(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cena
):
    auth_as(cena.admin)
    r = await client.post(
        "/api/pricing/accounts",
        json={"name": "atlas", "platform": "mercadolivre", "department": "celular"},
    )
    assert r.status_code == 201, r.text
    classico_id, par_id = r.json()["id"], r.json()["par_id"]
    url = f"/api/pricing/accounts/{classico_id}"
    r = await client.patch(url, json={"margem_minima_pct": 25})
    assert r.status_code == 200, r.text
    par = await _par(db, uuid.UUID(par_id))
    assert {linha.margem_minima_pct for linha in par.values()} == {D("25.00")}
    # Pela linha do Premium também grava nas duas.
    r = await client.patch(
        f"/api/pricing/accounts/{par[PREMIUM].id}", json={"margem_minima_pct": 20.5}
    )
    assert r.status_code == 200, r.text
    par = await _par(db, uuid.UUID(par_id))
    assert {linha.margem_minima_pct for linha in par.values()} == {D("20.50")}

    # Catálogo: mostra a Margem mínima da base e não se edita.
    r = await client.post(f"{url}/catalogo", json={"ativo": True})
    assert r.status_code == 200, r.text
    filha_id = r.json()["conta_catalogo"]["id"]
    assert D(r.json()["conta_catalogo"]["margem_minima_pct"]) == D("20.5")
    r = await client.patch(f"/api/pricing/accounts/{filha_id}", json={"margem_minima_pct": 10})
    assert r.status_code == 409, r.text
    r = await client.get("/api/pricing/accounts", params={"department": "celular"})
    contas = {c["id"]: c for c in r.json()}
    assert D(contas[filha_id]["margem_minima_pct"]) == D("20.5")
    # Quem não é ADM: a filha também sai sem a Margem mínima.
    auth_as(cena.vendedor)
    r = await client.get("/api/pricing/accounts", params={"department": "celular"})
    contas = {c["id"]: c for c in r.json()}
    assert contas[filha_id]["margem_minima_pct"] is None


# =========================================================== migration 0519


def _carregar():
    spec = importlib.util.spec_from_file_location("migration_0519", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _estrutura(db: AsyncSession, schema: str) -> dict[str, list]:
    colunas = (
        await db.execute(
            text(
                "SELECT column_name, data_type, numeric_precision, numeric_scale, is_nullable"
                " FROM information_schema.columns WHERE table_schema = :s"
                " AND table_name = 'pricing_accounts' AND column_name = 'margem_minima_pct'"
            ),
            {"s": schema},
        )
    ).all()
    checks = (
        await db.execute(
            text(
                "SELECT co.conname, pg_get_constraintdef(co.oid) FROM pg_constraint co"
                " JOIN pg_class cl ON cl.oid = co.conrelid"
                " JOIN pg_namespace n ON n.oid = cl.relnamespace"
                " WHERE n.nspname = :s AND cl.relname = 'pricing_accounts'"
                " AND co.conname = 'ck_pricing_accounts_margem_minima_pct'"
            ),
            {"s": schema},
        )
    ).all()
    return {"colunas": [tuple(r) for r in colunas], "checks": [tuple(r) for r in checks]}


async def test_migration_0519_cria_o_que_o_model_declara_e_o_downgrade_desfaz(
    db: AsyncSession,
):
    mod = _carregar()
    assert (mod.revision, mod.down_revision) == (
        "0519_contas_margem_minima",
        "0518_produto_regras_centro_custo",
    )
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    raiz = Path(__file__).resolve().parent.parent
    cfg = Config(str(raiz / "alembic.ini"))
    cfg.set_main_option("script_location", str(raiz / "alembic"))
    assert ScriptDirectory.from_config(cfg).get_heads() == ["0519_contas_margem_minima"]

    schema_model = Base.metadata.schema
    rascunho = f"{schema_model}_mig0519"
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    await db.execute(
        text(
            f'CREATE TABLE "{rascunho}".pricing_accounts'
            f' (LIKE "{schema_model}".pricing_accounts INCLUDING DEFAULTS)'
        )
    )
    await db.execute(
        text(f'ALTER TABLE "{rascunho}".pricing_accounts DROP COLUMN margem_minima_pct')
    )
    await db.commit()

    def _rodar_passo(conn, passo: str) -> None:
        ctx = MigrationContext.configure(conn, opts={"target_metadata": Base.metadata})
        with Operations.context(ctx):
            getattr(mod, passo)()

    mod.SCHEMA = rascunho
    try:
        conn = await db.connection()
        await conn.run_sync(_rodar_passo, "upgrade")
        await db.commit()
        da_migration = await _estrutura(db, rascunho)
        assert da_migration["colunas"] == [
            ("margem_minima_pct", "numeric", 5, 2, "YES")
        ]
        assert len(da_migration["checks"]) == 1
        assert da_migration == await _estrutura(db, schema_model)
        # O CHECK da migration segura 0..100 como o do model.
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".pricing_accounts (id, user_id, name, platform,'
                " segment_id, margem_minima_pct) VALUES (gen_random_uuid(), gen_random_uuid(),"
                " 'x', 'shopee', gen_random_uuid(), 100)"
            )
        )
        with pytest.raises(IntegrityError):
            await db.execute(
                text(
                    f'INSERT INTO "{rascunho}".pricing_accounts (id, user_id, name, platform,'
                    " segment_id, margem_minima_pct) VALUES (gen_random_uuid(),"
                    " gen_random_uuid(), 'x', 'shopee', gen_random_uuid(), 100.01)"
                )
            )
        await db.rollback()

        conn = await db.connection()
        await conn.run_sync(_rodar_passo, "downgrade")
        await db.commit()
        assert await _estrutura(db, rascunho) == {"colunas": [], "checks": []}
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()
