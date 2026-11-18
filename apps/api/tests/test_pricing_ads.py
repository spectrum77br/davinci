"""Ads da aba Contas da Tabela de Preços (M-12, 18/11/2026, migration 0503).

- A conversão da migration ("2% - 50 ROAS" → Geral 2, "0" → 0, o resto vai
  para a Obs) e a estrutura (a mesma do modelo; o downgrade desfaz).
- GET/PUT da Geral e do produto: campo ausente não muda, null apaga, produto
  sem % nem gasto volta para a Geral; validação 0–100 e gasto ≥ 0.
- Vê quem vê Contas (só as contas da cerca); grava quem edita Contas.
- "% gasto real" e "ROAS real" dos últimos 7 dias, lidos do Marketing pela
  plataforma + nome + departamento; sem correspondência ou sem dado: null.
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from httpx import AsyncClient
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.historico import nomes
from app.models import (
    Base,
    HistoricoAlteracao,
    PricingAccount,
    PricingAdsConta,
    PricingAdsProduto,
    PricingPlatform,
    PricingProduct,
    Segment,
    StoreInfo,
    UserRole,
)
from app.models.marketing import MarketingAccount, MarketingMetric
from app.security.jwt import issue_session_token
from app.services.pricing.anuncios import CANAL_CATALOGO
from tests.conftest import TEST_SCHEMA

pytestmark = pytest.mark.asyncio

_MIGRATION = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "0503_pricing_ads.py"

PERM_EDITA_CONTAS = {"tabela_precos_contas": {"view": True, "edit": True, "delete": False}}
PERM_SO_VE_CONTAS = {"tabela_precos_contas": {"view": True, "edit": False, "delete": False}}

SEM_REAL = {"pct_gasto": None, "roas": None}
PADRAO = {
    "ads_geral_ligada": True,
    "pode_gastar_pct": None,
    "gasto_dia": None,
    "ads_por_produto_ligada": True,
    "produtos": {},
    "real": SEM_REAL,
}


def _carregar():
    spec = importlib.util.spec_from_file_location("migration_0503", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# =========================================================== cenário


@pytest_asyncio.fixture
async def cenario(db: AsyncSession, make_user):
    """Celular: "londres ml" (Equipe 101, pela loja), a coluna de catálogo dela,
    "israel ml" (Equipe 102) e "aguiar" (Shopee, sem loja). Linhas cel_a e
    cel_b no Celular e mala_x na Mala. Contas de Ads do Marketing criadas no
    teste saem no fim (o conftest não limpa o Marketing)."""
    admin = await make_user(role=UserRole.ADMIN)
    raizes, folhas = {}, {}
    for ordem, slug in enumerate(("celular", "mala")):
        raiz = Segment(name=slug.title(), slug=slug, sort_order=ordem)
        db.add(raiz)
        await db.flush()
        folha = Segment(name=f"{slug}-1", slug=f"{slug}-1", sort_order=0, parent_id=raiz.id)
        db.add(folha)
        raizes[slug], folhas[slug] = raiz, folha
    await db.flush()
    loja_londres = StoreInfo(
        user_id=admin.id, platform="ml", account_name="londres ml", sales_team=101
    )
    loja_israel = StoreInfo(
        user_id=admin.id, platform="ml", account_name="israel ml", sales_team=102
    )
    db.add_all([loja_londres, loja_israel])
    await db.flush()

    def conta(nome: str, plataforma: PricingPlatform, **kw) -> PricingAccount:
        return PricingAccount(
            user_id=admin.id, name=nome, platform=plataforma, segment_id=raizes["celular"].id, **kw
        )

    londres = conta("londres ml", PricingPlatform.ML, store_info_id=loja_londres.id)
    israel = conta("israel ml", PricingPlatform.ML, store_info_id=loja_israel.id)
    aguiar = conta("Águiar", PricingPlatform.SHOPEE)
    db.add_all([londres, israel, aguiar])
    await db.flush()
    filha = conta("londres ml", PricingPlatform.ML, canal=CANAL_CATALOGO, conta_base_id=londres.id)
    db.add(filha)

    def linha(slug: str, sku: str, nome: str) -> PricingProduct:
        return PricingProduct(user_id=admin.id, sku=sku, name=nome, segment_id=folhas[slug].id)

    cel_a, cel_b = linha("celular", "cel-a", "Capa A"), linha("celular", "cel-b", "Brinde B")
    mala_x = linha("mala", "mala-x", "Mala X")
    db.add_all([cel_a, cel_b, mala_x])
    await db.commit()
    ns = SimpleNamespace(
        admin=admin,
        londres=londres,
        israel=israel,
        aguiar=aguiar,
        filha=filha,
        cel_a=cel_a,
        cel_b=cel_b,
        mala_x=mala_x,
        ads_marketing=[],
    )
    yield ns
    if ns.ads_marketing:
        await db.execute(delete(MarketingAccount).where(MarketingAccount.id.in_(ns.ads_marketing)))
        await db.commit()


async def _ver(client: AsyncClient, department: str = "celular"):
    return await client.get("/api/pricing/ads", params={"department": department})


async def _geral(client: AsyncClient, conta, **corpo):
    return await client.put(f"/api/pricing/ads/{conta.id}", json=corpo)


async def _produto(client: AsyncClient, conta, prod, **corpo):
    return await client.put(f"/api/pricing/ads/{conta.id}/produto/{prod.id}", json=corpo)


# =========================================================== GET / PUT


async def test_sem_nada_gravado_vem_o_padrao_e_os_produtos_do_departamento(
    client: AsyncClient, auth_as: Callable, cenario
):
    auth_as(cenario.admin)
    r = await _ver(client)
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["department"] == "celular"
    assert corpo["pode_editar"] is True
    # Só as linhas do Celular, por nome.
    assert [(p["sku"], p["name"]) for p in corpo["produtos"]] == [
        ("cel-b", "Brinde B"),
        ("cel-a", "Capa A"),
    ]
    # As contas da aba (sem a coluna de catálogo), todas no padrão.
    assert set(corpo["contas"]) == {
        str(cenario.londres.id),
        str(cenario.israel.id),
        str(cenario.aguiar.id),
    }
    assert all(c == PADRAO for c in corpo["contas"].values())
    assert (await _ver(client, "xyz")).status_code == 400


async def test_put_da_geral_campo_ausente_nao_muda_e_null_apaga(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cenario
):
    auth_as(cenario.admin)
    r = await _geral(client, cenario.londres, pode_gastar_pct=2, gasto_dia="50.5")
    assert r.status_code == 200, r.text
    assert r.json() == {**PADRAO, "pode_gastar_pct": 2.0, "gasto_dia": 50.5}
    r = await _geral(client, cenario.londres, ads_geral_ligada=False)
    assert (r.json()["pode_gastar_pct"], r.json()["ads_geral_ligada"]) == (2.0, False)
    # Caixa null não muda; % null apaga; o gasto fica.
    r = await _geral(client, cenario.londres, ads_por_produto_ligada=None, pode_gastar_pct=None)
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert (corpo["pode_gastar_pct"], corpo["gasto_dia"], corpo["ads_por_produto_ligada"]) == (
        None,
        50.5,
        True,
    )
    r = await _geral(client, cenario.londres, pode_gastar_pct=0, ads_por_produto_ligada=False)
    assert (r.json()["pode_gastar_pct"], r.json()["ads_por_produto_ligada"]) == (0.0, False)

    linha = (
        await db.execute(select(PricingAdsConta).execution_options(populate_existing=True))
    ).scalar_one()
    assert (linha.pricing_account_id, linha.pode_gastar_pct, linha.atualizado_por) == (
        cenario.londres.id,
        Decimal("0.00"),
        cenario.admin.id,
    )
    corpo = (await _ver(client)).json()
    assert corpo["contas"][str(cenario.londres.id)]["pode_gastar_pct"] == 0.0
    assert corpo["contas"][str(cenario.israel.id)] == PADRAO

    for invalido in (
        {"pode_gastar_pct": 101},
        {"pode_gastar_pct": -1},
        {"gasto_dia": -0.01},
        {"pode_gastar_pct": "abc"},
    ):
        r = await _geral(client, cenario.londres, **invalido)
        assert r.status_code == 422, (invalido, r.text)
    # Coluna de catálogo e conta que não existe: 404.
    assert (await _geral(client, cenario.filha, pode_gastar_pct=1)).status_code == 404
    r = await client.put(f"/api/pricing/ads/{uuid.uuid4()}", json={"pode_gastar_pct": 1})
    assert r.status_code == 404


async def test_put_do_produto_grava_e_sem_valor_volta_para_a_geral(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cenario
):
    auth_as(cenario.admin)
    a = str(cenario.cel_a.id)
    r = await _produto(client, cenario.londres, cenario.cel_a, pode_gastar_pct=5)
    assert r.status_code == 200, r.text
    assert r.json()["produtos"] == {a: {"pode_gastar_pct": 5.0, "gasto_dia": None}}
    r = await _produto(client, cenario.londres, cenario.cel_a, gasto_dia=30)
    assert r.json()["produtos"] == {a: {"pode_gastar_pct": 5.0, "gasto_dia": 30.0}}
    r = await _produto(client, cenario.londres, cenario.cel_a, pode_gastar_pct=None)
    assert r.json()["produtos"] == {a: {"pode_gastar_pct": None, "gasto_dia": 30.0}}
    # Sem % e sem gasto: a linha sai.
    r = await _produto(client, cenario.londres, cenario.cel_a, gasto_dia=None)
    assert r.json()["produtos"] == {}
    assert (await db.execute(select(PricingAdsProduto))).scalars().all() == []
    # Só gasto, direto.
    r = await _produto(client, cenario.londres, cenario.cel_b, gasto_dia="12.30")
    assert r.json()["produtos"] == {
        str(cenario.cel_b.id): {"pode_gastar_pct": None, "gasto_dia": 12.3}
    }
    # O % do produto não mexe na Geral nem na outra conta.
    corpo = (await _ver(client)).json()
    assert corpo["contas"][str(cenario.londres.id)]["pode_gastar_pct"] is None
    assert corpo["contas"][str(cenario.israel.id)] == PADRAO

    # Produto de outro departamento ou que não existe: 404; % fora de 0–100: 422.
    r = await _produto(client, cenario.londres, cenario.mala_x, pode_gastar_pct=1)
    assert (r.status_code, r.json()["detail"]) == (404, {"code": "product_not_found"})
    r = await client.put(
        f"/api/pricing/ads/{cenario.londres.id}/produto/{uuid.uuid4()}", json={"gasto_dia": 1}
    )
    assert r.status_code == 404
    r = await _produto(client, cenario.londres, cenario.cel_a, pode_gastar_pct=100.5)
    assert r.status_code == 422


async def test_quem_so_ve_contas_ve_sem_alterar_e_a_cerca_de_equipe_vale(
    db: AsyncSession, client: AsyncClient, make_user, auth_as: Callable, cenario
):
    auth_as(cenario.admin)
    await _geral(client, cenario.londres, pode_gastar_pct=3)

    leitor = await make_user(role=UserRole.USER, permissions=PERM_SO_VE_CONTAS)
    auth_as(leitor)
    corpo = (await _ver(client)).json()
    assert corpo["pode_editar"] is False
    assert corpo["contas"][str(cenario.londres.id)]["pode_gastar_pct"] == 3.0
    assert (await _geral(client, cenario.londres, pode_gastar_pct=9)).status_code == 403
    r = await _produto(client, cenario.londres, cenario.cel_a, pode_gastar_pct=9)
    assert r.status_code == 403

    sem_contas = await make_user(
        role=UserRole.USER, permissions={"tabela_precos": {"view": True, "edit": True}}
    )
    auth_as(sem_contas)
    assert (await _ver(client)).status_code == 403

    # Equipe 101: só as contas que a aba Contas dela mostra (a da loja dela;
    # "Águiar", sem loja nem integração, fica fora da cerca).
    londrino = await make_user(role=UserRole.USER, permissions=PERM_EDITA_CONTAS)
    londrino.sales_teams = [101]
    await db.commit()
    auth_as(londrino)
    corpo = (await _ver(client)).json()
    contas = (await client.get("/api/pricing/accounts", params={"department": "celular"})).json()
    da_aba = {c["id"] for c in contas if c.get("canal") != CANAL_CATALOGO}
    assert set(corpo["contas"]) == da_aba == {str(cenario.londres.id)}
    assert corpo["pode_editar"] is True
    assert (await _geral(client, cenario.israel, pode_gastar_pct=1)).status_code == 404
    r = await _geral(client, cenario.londres, gasto_dia=10)
    assert (r.status_code, r.json()["gasto_dia"]) == (200, 10.0)


# =========================================================== real do Marketing


async def test_real_dos_ultimos_7_dias_vem_do_marketing(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cenario
):
    agora = datetime.now(UTC)

    criadas: list[MarketingAccount] = []

    def ads(nome: str, plataforma: str, departamento: str, **kw) -> MarketingAccount:
        a = MarketingAccount(name=nome, platform=plataforma, department=departamento, **kw)
        db.add(a)
        criadas.append(a)
        return a

    londres = ads("Londres ML", "ml", "celular")  # casa com "londres ml" (ML, Celular)
    aguiar = ads("aguiar", "shopee", "celular")  # casa com "Águiar" (sem acento)
    # "israel ml": uma no departamento errado e duas iguais → nenhuma serve.
    ads("israel ml", "ml", "mala")
    ads("Israel-ML", "ml", "celular")
    ads("israel ml", "ml", "celular")
    # Arquivada não conta.
    ads("aguiar", "shopee", "celular", arquivada_em=agora)
    await db.flush()
    cenario.ads_marketing.extend(a.id for a in criadas)

    def dia(conta, dias_atras: float, gasto: float, receita: float, intensity: int = 0):
        db.add(
            MarketingMetric(
                account_id=conta.id,
                timestamp=agora - timedelta(days=dias_atras),
                spend=gasto,
                revenue=receita,
                intensity=intensity,
            )
        )

    dia(londres, 1, 10, 300)
    dia(londres, 6, 20, 700)
    dia(londres, 1.5, 999, 1, intensity=50)  # linha de hora: fora (o dia já soma)
    dia(londres, 10, 500, 500)  # fora da janela
    dia(aguiar, 2, 15, 0)  # gastou e não vendeu
    await db.commit()

    auth_as(cenario.admin)
    corpo = (await _ver(client)).json()
    reais = {k: v["real"] for k, v in corpo["contas"].items()}
    # 30 de gasto ÷ 1000 de faturamento: 3% e ROAS 33,33.
    assert reais[str(cenario.londres.id)] == {"pct_gasto": 3.0, "roas": 33.33}
    assert reais[str(cenario.aguiar.id)] == {"pct_gasto": None, "roas": 0.0}
    assert reais[str(cenario.israel.id)] == SEM_REAL
    # O PUT responde o real da conta também.
    r = await _geral(client, cenario.londres, pode_gastar_pct=2)
    assert r.json()["real"] == {"pct_gasto": 3.0, "roas": 33.33}
    # Mala: nenhuma conta de Ads casa (não há conta da Tabela lá).
    assert (await _ver(client, "mala")).json()["contas"] == {}


# =========================================================== Histórico


async def test_mudar_o_ads_fica_no_historico(client: AsyncClient, db: AsyncSession, cenario):
    """Pelo login de verdade (cookie): é o caminho que diz ao Histórico quem é."""
    eu_id = cenario.admin.id
    token, _, _ = issue_session_token(sub=cenario.admin.open_id, role=cenario.admin.role.value)
    client.cookies.set(get_settings().cookie_name, token)
    assert (await _geral(client, cenario.londres, pode_gastar_pct=2)).status_code == 200
    assert (await _produto(client, cenario.londres, cenario.cel_a, gasto_dia=40)).status_code == 200
    [criou] = (
        (
            await db.execute(
                select(HistoricoAlteracao).where(
                    HistoricoAlteracao.tabela == "pricing_ads_conta",
                    HistoricoAlteracao.operacao == "I",
                )
            )
        )
        .scalars()
        .all()
    )
    assert str(criou.ator_id) == str(eu_id)
    assert criou.depois["pode_gastar_pct"] == 2.0
    [produto] = (
        (
            await db.execute(
                select(HistoricoAlteracao).where(HistoricoAlteracao.tabela == "pricing_ads_produto")
            )
        )
        .scalars()
        .all()
    )
    assert produto.depois["gasto_dia"] == 40.0
    assert nomes.nome_tabela("pricing_ads_conta") == "Ads da conta (Tabela de preços)"
    assert nomes.nome_tabela("pricing_ads_produto") == "Ads do produto (Tabela de preços)"
    assert nomes.nome_campo("pode_gastar_pct") == "Ads: pode gastar (%)"
    assert nomes.nome_campo("gasto_dia") == "Ads: gasto por dia"


# =========================================================== migration 0503


async def test_conversao_dos_exemplos_do_pedido():
    mod = _carregar()
    assert mod.converter_ads("2% - 50 ROAS") == (Decimal("2"), None)
    assert mod.converter_ads("10% - 10 ROAS") == (Decimal("10"), None)
    # O ROAS digitado sai: agora é calculado (3% → 33,3).
    assert mod.converter_ads("3% - 30 ROAS") == (Decimal("3"), None)
    assert mod.converter_ads("0") == (Decimal("0"), None)
    assert mod.converter_ads("4,5%") == (Decimal("4.5"), None)
    assert mod.converter_ads(" 5 % ") == (Decimal("5"), None)
    for texto in ("3-4% - 33-25 - R$3000 25.08", "Sudeste R$ 50 Diario", "101", "muito"):
        assert mod.converter_ads(texto) == (None, f"Ads: {texto}"), texto
    assert mod.converter_ads(None) == (None, None)
    assert mod.converter_ads("  ") == (None, None)
    nota = "Ads: Sudeste R$ 50 Diario"
    assert mod.somar_obs("c68 | Afiliado: amostra", nota) == f"c68 | Afiliado: amostra | {nota}"
    assert mod.somar_obs(None, nota) == nota
    assert mod.somar_obs("c68", None) == "c68"
    assert mod.tirar_da_obs(f"c68 | {nota}", nota) == "c68"
    assert mod.tirar_da_obs(nota, nota) is None
    assert mod.tirar_da_obs("mexida depois", nota) == "mexida depois"


async def test_migration_converte_as_contas_e_o_downgrade_desfaz_a_obs(db: AsyncSession, cenario):
    mod = _carregar()
    mod.SCHEMA = TEST_SCHEMA
    cenario.londres.ads = "2% - 50 ROAS"
    cenario.israel.ads = "0"
    cenario.aguiar.ads = "Sudeste R$ 50 Diario"
    cenario.aguiar.observation = "c68"
    await db.commit()

    def _executar(conn, passo) -> None:
        with Operations.context(MigrationContext.configure(conn)):
            passo(conn)

    conn = await db.connection()
    await conn.run_sync(_executar, mod.converter)
    await db.commit()
    gerais = {
        g.pricing_account_id: g.pode_gastar_pct
        for g in (await db.execute(select(PricingAdsConta))).scalars()
    }
    assert gerais == {cenario.londres.id: Decimal("2.00"), cenario.israel.id: Decimal("0.00")}
    for c in (cenario.londres, cenario.israel, cenario.aguiar):
        await db.refresh(c)
    assert cenario.aguiar.observation == "c68 | Ads: Sudeste R$ 50 Diario"
    assert cenario.londres.observation is None
    # O texto antigo fica no banco.
    assert cenario.londres.ads == "2% - 50 ROAS"

    conn = await db.connection()
    await conn.run_sync(_executar, mod.desfazer_obs)
    await db.commit()
    await db.refresh(cenario.aguiar)
    assert cenario.aguiar.observation == "c68"


async def _estrutura(db: AsyncSession, schema: str, tabela: str) -> dict[str, list]:
    colunas = (
        await db.execute(
            text(
                "SELECT column_name, data_type, numeric_precision, numeric_scale, is_nullable,"
                " column_default FROM information_schema.columns"
                " WHERE table_schema = :s AND table_name = :t ORDER BY ordinal_position"
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
                " WHERE n.nspname = :s AND cl.relname = :t ORDER BY co.conname"
            ),
            {"s": schema, "t": tabela},
        )
    ).all()
    return {
        "colunas": [tuple(r) for r in colunas],
        "constraints": [(r[0], r[1].replace(f"{schema}.", "")) for r in constraints],
    }


async def test_migration_0503_cria_o_que_o_model_declara_e_o_downgrade_desfaz(db: AsyncSession):
    mod = _carregar()
    assert (mod.revision, mod.down_revision) == ("0503_pricing_ads", "0502_contas_campos_proprios")
    schema_model = Base.metadata.schema
    rascunho = f"{schema_model}_mig0503"
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    # Só o que a 0503 toca: as tabelas das FKs e o que a conversão lê.
    await db.execute(text(f'CREATE TABLE "{rascunho}".users (id uuid PRIMARY KEY)'))
    await db.execute(text(f'CREATE TABLE "{rascunho}".pricing_products (id uuid PRIMARY KEY)'))
    await db.execute(
        text(
            f'CREATE TABLE "{rascunho}".pricing_accounts'
            " (id uuid PRIMARY KEY, ads text, observation text)"
        )
    )
    conta, linha = str(uuid.uuid4()), str(uuid.uuid4())
    await db.execute(
        text(
            f"INSERT INTO \"{rascunho}\".pricing_accounts VALUES ('{conta}', '5% - 20 ROAS', null)"
        )
    )
    await db.execute(text(f"INSERT INTO \"{rascunho}\".pricing_products VALUES ('{linha}')"))
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
        for tabela in ("pricing_ads_conta", "pricing_ads_produto"):
            da_migration = await _estrutura(db, rascunho, tabela)
            assert da_migration["colunas"], tabela
            assert da_migration == await _estrutura(db, schema_model, tabela), tabela
        # A conversão: 5% na Geral, com as duas caixas ligadas.
        assert (
            await db.execute(
                text(
                    "SELECT pode_gastar_pct, gasto_dia, ads_geral_ligada, ads_por_produto_ligada"
                    f' FROM "{rascunho}".pricing_ads_conta'
                )
            )
        ).one() == (Decimal("5.00"), None, True, True)
        for tabela, valores in (
            ("pricing_ads_conta", "pode_gastar_pct = 100.01"),
            ("pricing_ads_conta", "gasto_dia = -1"),
        ):
            with pytest.raises(IntegrityError):
                await db.execute(text(f'UPDATE "{rascunho}".{tabela} SET {valores}'))
            await db.rollback()
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".pricing_ads_produto'
                " (pricing_account_id, pricing_product_id, gasto_dia)"
                f" VALUES ('{conta}', '{linha}', 9)"
            )
        )
        # Apagar a linha da Tabela leva o Ads do produto.
        await db.execute(text(f'DELETE FROM "{rascunho}".pricing_products'))
        assert (
            await db.execute(text(f'SELECT count(*) FROM "{rascunho}".pricing_ads_produto'))
        ).scalar_one() == 0
        await db.commit()

        conn = await db.connection()
        await conn.run_sync(_rodar_passo, "downgrade")
        await db.commit()
        restantes = (
            (
                await db.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables WHERE table_schema = :s"
                    ),
                    {"s": rascunho},
                )
            )
            .scalars()
            .all()
        )
        assert sorted(restantes) == ["pricing_accounts", "pricing_products", "users"]
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()
