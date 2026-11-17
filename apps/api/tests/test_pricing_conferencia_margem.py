"""Margem de erro da conferência no servidor e a conferência para quem vê a
Tabela (M-2/M-3, 17/11/2026).

Eduardo: a faixa roxa sai e a barra ganha "Margem 5% ▾" com [x] Geral [5] % e
[x] Por produto; o cartão vermelho e o "(!) anúncio X" aparecem para todas as
pessoas que veem a Tabela. Decisões do aprovador: a margem fica no servidor e
vale para todos; mudar a margem e conferir continuam só do administrador; o
GET da conferência respeita a cerca de equipe da grade.
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.historico import nomes
from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    Base,
    HistoricoAlteracao,
    Integration,
    IntegrationPlatform,
    PricingAccount,
    PricingConferencia,
    PricingConferenciaMargem,
    PricingConferenciaMargemProduto,
    PricingPlatform,
    PricingProduct,
    Segment,
    StoreInfo,
    User,
    UserRole,
)
from app.security.cipher import encrypt_json
from app.security.jwt import issue_session_token
from app.services.pricing.anuncios import CANAL_CATALOGO
from tests.conftest import TEST_SCHEMA

pytestmark = pytest.mark.asyncio

PERM_VER = {"tabela_precos": {"view": True, "edit": False, "delete": False}}
PERM_TUDO = {"tabela_precos": {"view": True, "edit": True, "delete": True}}

MARGEM_PADRAO = {
    "margem_pct": 5.0,
    "geral_ligada": True,
    "por_produto_ligada": True,
    "produtos": {},
}


@pytest_asyncio.fixture(autouse=True)
async def _tipo_do_job_no_schema_de_teste(db: AsyncSession):
    """O conftest cria `background_job_type` com a lista fixa de antes; a 0442
    acrescenta 'conferir_precos' — aqui também (o GET procura o último job)."""
    await db.execute(
        text(
            f'ALTER TYPE "{TEST_SCHEMA}".background_job_type '
            "ADD VALUE IF NOT EXISTS 'conferir_precos'"
        )
    )
    await db.commit()


# =========================================================== cenário


async def _segmentos(db: AsyncSession) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for ordem, slug in enumerate(("celular", "mala")):
        raiz = Segment(name=slug.title(), slug=slug, sort_order=ordem)
        db.add(raiz)
        await db.flush()
        filhos = []
        for i in range(5):
            f = Segment(
                name=f"{slug}-{i + 1}", slug=f"{slug}-{i + 1}", sort_order=i, parent_id=raiz.id
            )
            db.add(f)
            filhos.append(f)
        await db.flush()
        out[slug] = raiz
        out[f"{slug}_filhos"] = filhos
    return out


@pytest_asyncio.fixture
async def admin(make_user) -> User:
    return await make_user(role=UserRole.ADMIN)


@pytest_asyncio.fixture
async def cenario(db: AsyncSession, admin: User) -> SimpleNamespace:
    """Celular com duas equipes:
      - Londres (101): "londres ml" (integração da loja), a coluna de catálogo
        dela e "londres shopee" (só pela loja, `store_info_id`);
      - Israel (102): "israel ml" (integração da loja) e o catálogo dela.
    Linhas: cel_a e cel_b no Celular, mala_x na Mala. Uma conferência gravada
    em cada conta × cel_a, e o último job com o resumo (nome de integração)."""
    seg = await _segmentos(db)
    cel = seg["celular"]

    def integracao(nome: str) -> Integration:
        return Integration(
            user_id=admin.id,
            platform=IntegrationPlatform.ML,
            name=nome,
            credentials=encrypt_json({"access_token": "tok-teste"}),
        )

    integ_londres, integ_israel = integracao("londres ml"), integracao("israel ml")
    db.add_all([integ_londres, integ_israel])
    await db.flush()
    loja_londres = StoreInfo(
        user_id=admin.id,
        platform="ml",
        account_name="londres ml",
        sales_team=101,
        integration_id=integ_londres.id,
    )
    loja_londres_sh = StoreInfo(
        user_id=admin.id, platform="shopee", account_name="londres shopee", sales_team=101
    )
    loja_israel = StoreInfo(
        user_id=admin.id,
        platform="ml",
        account_name="israel ml",
        sales_team=102,
        integration_id=integ_israel.id,
    )
    db.add_all([loja_londres, loja_londres_sh, loja_israel])
    await db.flush()

    def conta(nome: str, plataforma: PricingPlatform, **kw) -> PricingAccount:
        return PricingAccount(
            user_id=admin.id,
            name=nome,
            platform=plataforma,
            segment_id=cel.id,
            commission=Decimal("0.10"),
            margin1=Decimal("0.20"),
            **kw,
        )

    londres = conta(
        "londres ml",
        PricingPlatform.ML,
        integration_id=integ_londres.id,
        listing_type="ml classico",
    )
    londres_sh = conta("londres shopee", PricingPlatform.SHOPEE, store_info_id=loja_londres_sh.id)
    israel = conta(
        "israel ml", PricingPlatform.ML, integration_id=integ_israel.id, listing_type="ml classico"
    )
    db.add_all([londres, londres_sh, israel])
    await db.flush()
    filha_londres = conta(
        "londres ml",
        PricingPlatform.ML,
        canal=CANAL_CATALOGO,
        conta_base_id=londres.id,
        listing_type="ml classico",
    )
    filha_israel = conta(
        "israel ml",
        PricingPlatform.ML,
        canal=CANAL_CATALOGO,
        conta_base_id=israel.id,
        listing_type="ml classico",
    )
    db.add_all([filha_londres, filha_israel])

    def linha(folha: Segment, sku: str) -> PricingProduct:
        return PricingProduct(
            user_id=admin.id, sku=sku, name=f"Linha {sku}", segment_id=folha.id,
            cost_kit1=Decimal("40"),
        )

    cel_a = linha(seg["celular_filhos"][0], "cel-a")
    cel_b = linha(seg["celular_filhos"][1], "cel-b")
    mala_x = linha(seg["mala_filhos"][0], "mala-x")
    db.add_all([cel_a, cel_b, mala_x])
    await db.flush()

    job = BackgroundJob(
        type=BackgroundJobType.CONFERIR_PRECOS,
        status=BackgroundJobStatus.SUCCEEDED,
        created_by=admin.id,
        payload={"department": "celular"},
        finished_at=datetime.now(UTC),
        result={
            "celulas": 5,
            "contas_com_erro": [{"conta": "israel ml", "motivo": "token vencido"}],
        },
    )
    db.add(job)
    await db.flush()
    contas = [londres, londres_sh, israel, filha_londres, filha_israel]
    for c in contas:
        db.add(
            PricingConferencia(
                pricing_account_id=c.id,
                pricing_product_id=cel_a.id,
                department="celular",
                preco_tabela=Decimal("59"),
                situacao="conferida",
                anuncios=[{"anuncio": f"MLB-{c.name}", "preco": 59.0, "ativo": True}],
                job_id=job.id,
            )
        )
    await db.commit()
    return SimpleNamespace(
        seg=seg,
        londres=londres,
        londres_sh=londres_sh,
        israel=israel,
        filha_londres=filha_londres,
        filha_israel=filha_israel,
        contas=contas,
        cel_a=cel_a,
        cel_b=cel_b,
        mala_x=mala_x,
        job=job,
    )


async def _ver(client: AsyncClient, department: str = "celular"):
    return await client.get("/api/pricing/conferencia", params={"department": department})


async def _geral(client: AsyncClient, **corpo):
    return await client.put(
        "/api/pricing/conferencia/margem", json={"department": "celular", **corpo}
    )


async def _do_produto(client: AsyncClient, prod_id, margem):
    return await client.put(
        f"/api/pricing/conferencia/margem/produto/{prod_id}", json={"margem_pct": margem}
    )


# =========================================================== margem


async def test_sem_nada_gravado_vale_5_com_as_duas_caixas(
    client: AsyncClient, admin: User, cenario, auth_as: Callable
):
    auth_as(admin)
    r = await _ver(client)
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["margem"] == {"department": "celular", **MARGEM_PADRAO, "pode_editar": True}
    assert corpo["pode_conferir"] is True
    r = await _ver(client, "mala")
    assert r.json()["margem"] == {"department": "mala", **MARGEM_PADRAO, "pode_editar": True}


async def test_put_geral_e_as_caixas_campo_ausente_nao_muda(
    db: AsyncSession, client: AsyncClient, admin: User, cenario, auth_as: Callable
):
    auth_as(admin)
    r = await _geral(client, margem_pct=7.5)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "department": "celular",
        "margem_pct": 7.5,
        "geral_ligada": True,
        "por_produto_ligada": True,
        "produtos": {},
        "pode_editar": True,
    }
    r = await _geral(client, geral_ligada=False)
    assert (r.json()["margem_pct"], r.json()["geral_ligada"], r.json()["por_produto_ligada"]) == (
        7.5,
        False,
        True,
    )
    r = await _geral(client, por_produto_ligada=False, margem_pct=0)
    assert (r.json()["margem_pct"], r.json()["geral_ligada"], r.json()["por_produto_ligada"]) == (
        0.0,
        False,
        False,
    )
    # Só o departamento (ou campos null): nada muda.
    r = await _geral(client, margem_pct=None, geral_ligada=None)
    assert r.status_code == 200
    assert (r.json()["margem_pct"], r.json()["geral_ligada"]) == (0.0, False)
    r = await _geral(client, margem_pct=100, geral_ligada=True)
    assert (r.json()["margem_pct"], r.json()["geral_ligada"]) == (100.0, True)

    linha = (
        await db.execute(
            select(PricingConferenciaMargem).execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert (linha.department, linha.margem_pct, linha.atualizado_por) == (
        "celular",
        Decimal("100.00"),
        admin.id,
    )
    # O GET devolve o mesmo; a Mala segue no padrão.
    assert (await _ver(client)).json()["margem"]["margem_pct"] == 100.0
    assert (await _ver(client, "mala")).json()["margem"]["margem_pct"] == 5.0


async def test_put_do_produto_grava_atualiza_e_null_apaga(
    db: AsyncSession, client: AsyncClient, admin: User, cenario, auth_as: Callable
):
    auth_as(admin)
    a, x = str(cenario.cel_a.id), str(cenario.mala_x.id)
    r = await _do_produto(client, a, 12)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "department": "celular",
        "margem_pct": 5.0,
        "geral_ligada": True,
        "por_produto_ligada": True,
        "produtos": {a: 12.0},
        "pode_editar": True,
    }
    # Produto da Mala responde a margem da Mala, e cada aba só traz os dela.
    r = await _do_produto(client, x, 3.25)
    assert (r.json()["department"], r.json()["produtos"]) == ("mala", {x: 3.25})
    assert (await _ver(client)).json()["margem"]["produtos"] == {a: 12.0}
    # Atualiza a mesma linha.
    r = await _do_produto(client, a, 15)
    assert r.json()["produtos"] == {a: 15.0}
    linha = await db.get(PricingConferenciaMargemProduto, cenario.cel_a.id)
    assert (linha.margem_pct, linha.atualizado_por) == (Decimal("15.00"), admin.id)
    # null apaga (volta para a Geral); de novo não dá erro.
    r = await _do_produto(client, a, None)
    assert (r.status_code, r.json()["produtos"]) == (200, {})
    r = await _do_produto(client, a, None)
    assert (r.status_code, r.json()["produtos"]) == (200, {})
    # Apagar a linha da Tabela leva a margem dela.
    await db.delete(await db.get(PricingProduct, cenario.mala_x.id))
    await db.commit()
    assert (
        await db.execute(select(PricingConferenciaMargemProduto))
    ).scalars().all() == []


async def test_validacoes_da_margem(
    db: AsyncSession, client: AsyncClient, admin: User, cenario, auth_as: Callable
):
    auth_as(admin)
    for fora in (-1, -0.01, 100.01, 101):
        assert (await _geral(client, margem_pct=fora)).status_code == 422, fora
        assert (await _do_produto(client, cenario.cel_a.id, fora)).status_code == 422, fora
    assert (await _geral(client, margem_pct="abc")).status_code == 422
    # O corpo do produto precisa dizer a margem (null é apagar, de propósito).
    r = await client.put(f"/api/pricing/conferencia/margem/produto/{cenario.cel_a.id}", json={})
    assert r.status_code == 422
    # Departamento que não existe: a mesma resposta do GET.
    r = await client.put("/api/pricing/conferencia/margem", json={"department": "xyz"})
    assert (r.status_code, r.json()["detail"]) == (400, {"code": "invalid_department"})
    r = await _do_produto(client, uuid.uuid4(), 5)
    assert (r.status_code, r.json()["detail"]) == (404, {"code": "product_not_found"})
    # Nada foi gravado.
    assert (await db.execute(select(PricingConferenciaMargem))).scalars().all() == []
    assert (await db.execute(select(PricingConferenciaMargemProduto))).scalars().all() == []
    # Os limites valem.
    assert (await _geral(client, margem_pct=0)).status_code == 200
    assert (await _do_produto(client, cenario.cel_a.id, 100)).status_code == 200


async def test_so_o_administrador_muda_a_margem(
    db: AsyncSession, client: AsyncClient, make_user, cenario, auth_as: Callable
):
    vendedor = await make_user(role=UserRole.USER, permissions=PERM_TUDO)
    auth_as(vendedor)
    r = await _geral(client, margem_pct=9)
    assert (r.status_code, r.json()["detail"]) == (403, {"code": "admin_only"})
    r = await _do_produto(client, cenario.cel_a.id, 9)
    assert (r.status_code, r.json()["detail"]) == (403, {"code": "admin_only"})
    assert (await db.execute(select(PricingConferenciaMargem))).scalars().all() == []
    assert (await db.execute(select(PricingConferenciaMargemProduto))).scalars().all() == []
    # Ele vê a margem, mas não pode editar nem conferir.
    corpo = (await _ver(client)).json()
    assert corpo["margem"] == {"department": "celular", **MARGEM_PADRAO, "pode_editar": False}
    assert corpo["pode_conferir"] is False


# =========================================================== GET para quem vê a Tabela


def _contas_das_celulas(corpo: dict) -> set[str]:
    return {c["pricing_account_id"] for c in corpo["celulas"]}


async def test_get_para_quem_ve_a_tabela_so_traz_as_contas_da_equipe(
    db: AsyncSession, client: AsyncClient, admin: User, make_user, cenario, auth_as: Callable
):
    londrino = await make_user(role=UserRole.USER, permissions=PERM_VER)
    londrino.sales_teams = [101]
    await db.commit()
    auth_as(londrino)
    r = await _ver(client)
    assert r.status_code == 200, r.text
    corpo = r.json()
    # Londres: a conta da integração, a da loja e o catálogo que vai com a base.
    esperadas = {
        str(cenario.londres.id),
        str(cenario.londres_sh.id),
        str(cenario.filha_londres.id),
    }
    assert _contas_das_celulas(corpo) == esperadas
    # A mesma régua da grade: as colunas que ele vê são as das células.
    grade = (await client.get("/api/pricing/grid", params={"department": "celular"})).json()
    assert {a["id"] for a in grade["accounts"]} == esperadas
    assert corpo["pode_conferir"] is False
    assert corpo["margem"]["pode_editar"] is False
    # O job vem (a tela compara com o job das células), sem o resumo do
    # departamento inteiro (nome de integração de outra equipe).
    assert corpo["job"]["id"] == str(cenario.job.id)
    assert corpo["job"]["result"] == {}
    assert corpo["ultimo_concluido"] == str(cenario.job.id)

    # Quem tem "todas as contas" na Tabela vê todas, como na grade.
    israel_todas = await make_user(
        role=UserRole.USER,
        permissions={
            **PERM_VER,
            "tabela_precos_todas_contas": {"view": True, "edit": False, "delete": False},
        },
    )
    israel_todas.sales_teams = [102]
    await db.commit()
    auth_as(israel_todas)
    assert _contas_das_celulas((await _ver(client)).json()) == {str(c.id) for c in cenario.contas}

    # O administrador continua recebendo tudo, com o resumo do job.
    auth_as(admin)
    corpo = (await _ver(client)).json()
    assert _contas_das_celulas(corpo) == {str(c.id) for c in cenario.contas}
    assert corpo["job"]["result"]["contas_com_erro"] == [
        {"conta": "israel ml", "motivo": "token vencido"}
    ]
    assert corpo["pode_conferir"] is True


async def test_get_exige_ver_a_tabela_e_conferir_continua_do_administrador(
    client: AsyncClient, make_user, cenario, auth_as: Callable
):
    sem_tabela = await make_user(
        role=UserRole.USER,
        permissions={"tabela_precos_contas": {"view": True, "edit": True, "delete": True}},
    )
    auth_as(sem_tabela)
    r = await _ver(client)
    assert (r.status_code, r.json()["detail"]["code"]) == (403, "forbidden")

    vendedor = await make_user(role=UserRole.USER, permissions=PERM_TUDO)
    auth_as(vendedor)
    r = await client.post("/api/pricing/conferencia", json={"department": "celular"})
    assert (r.status_code, r.json()["detail"]) == (403, {"code": "admin_only"})
    # Departamento inválido continua 400 para quem vê.
    assert (await _ver(client, "xyz")).status_code == 400


# =========================================================== Histórico


async def test_mudar_a_margem_fica_no_historico(client: AsyncClient, db: AsyncSession, make_user):
    """Pelo login de verdade (cookie): é o caminho que diz ao Histórico quem é."""
    eu = await make_user(role=UserRole.ADMIN)
    eu_id = eu.id
    await _segmentos(db)
    await db.commit()
    token, _, _ = issue_session_token(sub=eu.open_id, role=eu.role.value)
    client.cookies.set(get_settings().cookie_name, token)
    r = await _geral(client, margem_pct=8)
    assert r.status_code == 200, r.text
    r = await _geral(client, margem_pct=6)
    assert r.status_code == 200, r.text
    [criou] = (
        await db.execute(
            select(HistoricoAlteracao).where(
                HistoricoAlteracao.tabela == "pricing_conferencia_margem",
                HistoricoAlteracao.operacao == "I",
            )
        )
    ).scalars().all()
    assert str(criou.ator_id) == str(eu_id)
    assert criou.depois["margem_pct"] == 8.0
    [mudou] = (
        await db.execute(
            select(HistoricoAlteracao).where(
                HistoricoAlteracao.tabela == "pricing_conferencia_margem",
                HistoricoAlteracao.operacao == "U",
            )
        )
    ).scalars().all()
    assert (mudou.antes["margem_pct"], mudou.depois["margem_pct"]) == (8.0, 6.0)
    assert nomes.nome_tabela("pricing_conferencia_margem") == (
        "Margem de erro geral (Tabela de preços)"
    )
    assert nomes.nome_tabela("pricing_conferencia_margem_produto") == (
        "Margem de erro do produto (Tabela de preços)"
    )
    assert nomes.nome_campo("margem_pct") == "Margem de erro (%)"


# =========================================================== migration 0499

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "0499_pricing_conferencia_margem.py"
)
_TABELAS = ("pricing_conferencia_margem", "pricing_conferencia_margem_produto")


def _carregar_migration():
    spec = importlib.util.spec_from_file_location("migration_0499", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _estrutura(db: AsyncSession, schema: str, tabela: str) -> dict[str, list]:
    colunas = (
        await db.execute(
            text(
                "SELECT column_name, data_type, numeric_precision, numeric_scale, is_nullable,"
                " column_default FROM information_schema.columns"
                " WHERE table_schema = :s AND table_name = :t"
                " ORDER BY ordinal_position"
            ),
            {"s": schema, "t": tabela},
        )
    ).all()
    constraints = (
        await db.execute(
            text(
                "SELECT co.conname, pg_get_constraintdef(co.oid) FROM pg_constraint co"
                " JOIN pg_class cl ON cl.oid = co.conrelid"
                " JOIN pg_namespace n ON n.oid = cl.relnamespace"
                " WHERE n.nspname = :s AND cl.relname = :t"
                " ORDER BY co.conname"
            ),
            {"s": schema, "t": tabela},
        )
    ).all()
    indices = (
        await db.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes"
                " WHERE schemaname = :s AND tablename = :t"
                " ORDER BY indexname"
            ),
            {"s": schema, "t": tabela},
        )
    ).all()
    return {
        "colunas": [tuple(r) for r in colunas],
        "constraints": [(r[0], r[1].replace(f"{schema}.", "")) for r in constraints],
        "indices": [(r[0], r[1].replace(f"{schema}.", "")) for r in indices],
    }


async def test_migration_0499_cria_o_que_o_model_declara_e_o_downgrade_desfaz(db: AsyncSession):
    mod = _carregar_migration()
    assert mod.revision == "0499_pricing_conferencia_margem"
    assert mod.down_revision == "0498_carrefour_offer_write"
    schema_model = Base.metadata.schema
    rascunho = f"{schema_model}_mig0499"
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    # Só o que a 0499 toca: as tabelas das FKs.
    for tabela in ("users", "pricing_products"):
        await db.execute(text(f'CREATE TABLE "{rascunho}".{tabela} (id uuid PRIMARY KEY)'))
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

        for tabela in _TABELAS:
            da_migration = await _estrutura(db, rascunho, tabela)
            assert da_migration["colunas"], tabela
            assert da_migration == await _estrutura(db, schema_model, tabela), tabela

        pessoa, linha = str(uuid.uuid4()), str(uuid.uuid4())
        await db.execute(text(f"INSERT INTO \"{rascunho}\".users VALUES ('{pessoa}')"))
        await db.execute(text(f"INSERT INTO \"{rascunho}\".pricing_products VALUES ('{linha}')"))
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".pricing_conferencia_margem'
                f" (department, atualizado_por) VALUES ('celular', '{pessoa}')"
            )
        )
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".pricing_conferencia_margem_produto'
                f" (pricing_product_id, margem_pct, atualizado_por)"
                f" VALUES ('{linha}', 12.5, '{pessoa}')"
            )
        )
        await db.commit()
        # Sem nada dito: 5% e as duas caixas ligadas.
        assert (
            await db.execute(
                text(
                    "SELECT margem_pct, geral_ligada, por_produto_ligada,"
                    " atualizado_em IS NOT NULL"
                    f' FROM "{rascunho}".pricing_conferencia_margem'
                )
            )
        ).one() == (Decimal("5.00"), True, True, True)
        for tabela in _TABELAS:
            with pytest.raises(IntegrityError):
                await db.execute(text(f'UPDATE "{rascunho}".{tabela} SET margem_pct = 100.01'))
            await db.rollback()
        # Apagar a pessoa zera quem mudou; apagar a linha da Tabela leva a margem.
        await db.execute(text(f'DELETE FROM "{rascunho}".users'))
        assert (
            await db.execute(
                text(f'SELECT atualizado_por FROM "{rascunho}".pricing_conferencia_margem')
            )
        ).scalar_one() is None
        await db.execute(text(f'DELETE FROM "{rascunho}".pricing_products'))
        assert (
            await db.execute(
                text(f'SELECT count(*) FROM "{rascunho}".pricing_conferencia_margem_produto')
            )
        ).scalar_one() == 0
        await db.commit()

        conn = await db.connection()
        await conn.run_sync(_rodar_passo, "downgrade")
        await db.commit()
        for tabela in _TABELAS:
            assert await _estrutura(db, rascunho, tabela) == {
                "colunas": [],
                "constraints": [],
                "indices": [],
            }
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()
