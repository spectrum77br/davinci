"""Aba Contas: conta ML em par Clássico + Premium (M-53/M-54), Desconto %
(M-55) e Custo total % (M-56) — migration 0511.

- Criar conta ML cria as DUAS linhas (mesmo par_id) com a comissão usual da
  categoria; o corpo não escolhe tipo nem comissão.
- Gravar numa linha do par grava na irmã (menos comissão e ordem); o tipo e a
  plataforma do par não mudam; o Histórico registra as duas linhas.
- PUT /comissao-ml grava as duas comissões (só o ADM); excluir exclui as duas;
  o catálogo liga/desliga nas duas e a margem dele vale para as duas; o Ads
  grava nas duas.
- Custo total: Desconto + o maior entre Afiliado e Ads ≤ teto (M-94: a venda
  vem por Ads ou por afiliado, nunca os dois), conferido no servidor com os
  valores novos (PATCH, PUT do Ads da conta e do produto); quem não é ADM vê
  mas não grava.
- A migration de dados: pareia todos os pares, harmoniza só os visíveis e
  ABORTA numa diferença fora do previsto. Contas FABRICADAS no schema de teste.
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
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

from app.config import get_settings
from app.historico import nomes
from app.models import (
    Base,
    HistoricoAlteracao,
    Integration,
    IntegrationPlatform,
    PricingAccount,
    PricingAdsConta,
    PricingAdsProduto,
    PricingPlatform,
    PricingProduct,
    Segment,
    StoreInfo,
    UserRole,
)
from app.security.jwt import issue_session_token
from app.services.pricing.anuncios import CANAL_CATALOGO
from app.services.pricing.custo_total import EstadoAds, estouro, pct_br

pytestmark = pytest.mark.asyncio

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "0511_contas_ml_par_custo_total.py"
)

PERM_EDITA_CONTAS = {
    "tabela_precos": {"view": True, "edit": False, "delete": False},
    "tabela_precos_contas": {"view": True, "edit": True, "delete": True},
}
CLASSICO, PREMIUM = "ml classico", "ml premium"


def _carregar():
    spec = importlib.util.spec_from_file_location("migration_0511", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.SCHEMA = Base.metadata.schema
    return mod


async def _rodar(db: AsyncSession, passo) -> object:
    def _executar(conn):
        with Operations.context(MigrationContext.configure(conn)):
            return passo(conn)

    conn = await db.connection()
    return await conn.run_sync(_executar)


# =========================================================== cenário


@pytest_asyncio.fixture
async def cena(db: AsyncSession, make_user) -> SimpleNamespace:
    """Raízes celular, mala, eletro e "outra", cada uma com dois tipos; a loja
    ML "forpaper" (Equipe 101) e uma linha de produto no Celular."""
    admin = await make_user(role=UserRole.ADMIN)
    vendedor = await make_user(role=UserRole.USER, permissions=PERM_EDITA_CONTAS)
    raizes: dict[str, Segment] = {}
    folhas: dict[str, list[Segment]] = {}
    for ordem, slug in enumerate(("celular", "mala", "eletro", "outra")):
        raiz = Segment(name=slug.title(), slug=slug, sort_order=ordem)
        db.add(raiz)
        await db.flush()
        folhas[slug] = []
        for n in range(2):
            folha = Segment(
                name=f"{slug}-{n + 1}", slug=f"{slug}-{n + 1}", sort_order=n, parent_id=raiz.id
            )
            db.add(folha)
            folhas[slug].append(folha)
        raizes[slug] = raiz
    loja = StoreInfo(user_id=admin.id, platform="ml", account_name="forpaper", sales_team=101)
    db.add(loja)
    await db.flush()
    produto = PricingProduct(
        user_id=admin.id, sku="cel-a", name="Capa A", segment_id=folhas["celular"][0].id
    )
    produto_b = PricingProduct(
        user_id=admin.id, sku="cel-b", name="Capa B", segment_id=folhas["celular"][1].id
    )
    db.add_all([produto, produto_b])
    await db.commit()
    return SimpleNamespace(
        admin=admin,
        vendedor=vendedor,
        raizes=raizes,
        folhas=folhas,
        loja=loja,
        produto=produto,
        produto_b=produto_b,
    )


async def _criar_ml(client: AsyncClient, nome: str = "forpaper", dept: str = "celular", **kw):
    r = await client.post(
        "/api/pricing/accounts",
        json={"name": nome, "platform": "mercadolivre", "department": dept, **kw},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _par(db: AsyncSession, par_id) -> dict[str, PricingAccount]:
    linhas = (
        (
            await db.execute(
                select(PricingAccount)
                .where(PricingAccount.par_id == uuid.UUID(str(par_id)))
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    return {linha.listing_type: linha for linha in linhas}


# =========================================================== criar


async def test_criar_conta_ml_cria_classico_e_premium_com_a_comissao_da_categoria(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cena
):
    auth_as(cena.admin)
    esperado = {
        "celular": (Decimal("0.11"), Decimal("0.16")),
        "eletro": (Decimal("0.11"), Decimal("0.16")),
        "mala": (Decimal("0.14"), Decimal("0.19")),
        "outra": (Decimal("0.11"), Decimal("0.16")),
    }
    for dept, (com_c, com_p) in esperado.items():
        # O corpo não escolhe tipo nem comissão.
        corpo = await _criar_ml(
            client,
            f"conta {dept}",
            dept,
            listing_type="ml premium",
            commission="0.50",
            kit_number=2,
            afiliado_pct=4,
        )
        assert corpo["listing_type"] == CLASSICO
        assert Decimal(corpo["commission"]) == com_c
        assert corpo["par_id"] is not None
        par = await _par(db, corpo["par_id"])
        assert set(par) == {CLASSICO, PREMIUM}
        assert str(par[CLASSICO].id) == corpo["id"]
        assert (par[CLASSICO].commission, par[PREMIUM].commission) == (com_c, com_p)
        for linha in par.values():
            assert linha.name == f"conta {dept}"
            assert linha.kit_number == 2
            assert linha.afiliado_pct == Decimal("4.00")
            assert linha.segment_id == cena.raizes[dept].id
            # Os slots default da aba nas duas.
            assert linha.slot1_segment_id == cena.folhas[dept][0].id
            assert linha.slot2_segment_id == cena.folhas[dept][1].id

    # Outra plataforma: uma linha só, sem par, como antes.
    r = await client.post(
        "/api/pricing/accounts",
        json={"name": "aguiar", "platform": "shopee", "department": "celular", "commission": "0.2"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["par_id"] is None
    assert Decimal(r.json()["commission"]) == Decimal("0.2")
    total = (await db.execute(select(PricingAccount))).scalars().all()
    assert len(total) == 9
    # Nenhuma linha do ML sem tipo.
    assert all(a.listing_type for a in total if a.platform == PricingPlatform.ML)


async def test_lojas_ligar_departamento_ml_cria_o_par(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cena
):
    """Cadastros › Lojas › departamento numa loja ML sem conta: nasce o par
    (nunca mais uma linha ML sem tipo)."""
    auth_as(cena.admin)
    r = await client.post(
        f"/api/pricing/store-info/{cena.loja.id}/department", json={"department": "mala"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["listing_type"] == CLASSICO
    par = await _par(db, r.json()["par_id"])
    assert {t: (a.commission, a.store_info_id) for t, a in par.items()} == {
        CLASSICO: (Decimal("0.14"), cena.loja.id),
        PREMIUM: (Decimal("0.19"), cena.loja.id),
    }


# =========================================================== editar


async def test_patch_numa_linha_do_par_grava_na_irma(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cena
):
    auth_as(cena.admin)
    corpo = await _criar_ml(client)
    par = await _par(db, corpo["par_id"])
    premium_id = par[PREMIUM].id
    r = await client.patch(
        f"/api/pricing/accounts/{corpo['id']}",
        json={
            "name": "forpaper 2",
            "kit_number": 3,
            "margin1": "0.25",
            "shipping2": "7.5",
            "observation": "nota",
            "store_info_id": str(cena.loja.id),
            "afiliado_pct": 4,
            "desconto_pct": 2.5,
            "desconto_ate": "19/06",
            "oferta_limite_pct": 5,
            # Comissão e ordem ficam só nesta linha.
            "commission": "0.12",
            "sort_order": 7,
        },
    )
    assert r.status_code == 200, r.text
    assert Decimal(r.json()["desconto_pct"]) == Decimal("2.5")
    par = await _par(db, corpo["par_id"])
    for linha in par.values():
        assert linha.name == "forpaper 2"
        assert linha.kit_number == 3
        assert linha.margin1 == Decimal("0.2500")
        assert linha.shipping2 == Decimal("7.50")
        assert linha.observation == "nota"
        assert linha.store_info_id == cena.loja.id
        assert (linha.afiliado_pct, linha.desconto_pct) == (Decimal("4.00"), Decimal("2.50"))
        assert linha.desconto_ate == "19/06"
        assert linha.oferta_limite_pct == Decimal("5.00")
    assert (par[CLASSICO].commission, par[PREMIUM].commission) == (
        Decimal("0.1200"),
        Decimal("0.1600"),
    )
    assert (par[CLASSICO].sort_order, par[PREMIUM].sort_order) == (7, 0)

    # Pela irmã também vale (o id pode ser de qualquer uma).
    r = await client.patch(f"/api/pricing/accounts/{premium_id}", json={"observation": None})
    assert r.status_code == 200, r.text
    assert {linha.observation for linha in (await _par(db, corpo["par_id"])).values()} == {None}


async def test_par_muda_de_aba_junto_com_os_slots_de_cada_linha(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cena
):
    auth_as(cena.admin)
    corpo = await _criar_ml(client)
    # Catálogo ligado nas duas: as colunas de catálogo acompanham.
    r = await client.post(f"/api/pricing/accounts/{corpo['id']}/catalogo", json={"ativo": True})
    assert r.status_code == 200, r.text
    r = await client.patch(f"/api/pricing/accounts/{corpo['id']}", json={"department": "mala"})
    assert r.status_code == 200, r.text
    par = await _par(db, corpo["par_id"])
    for linha in par.values():
        assert linha.segment_id == cena.raizes["mala"].id
        assert linha.slot1_segment_id == cena.folhas["mala"][0].id
        filha = (
            await db.execute(
                select(PricingAccount)
                .where(PricingAccount.conta_base_id == linha.id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert filha.segment_id == cena.raizes["mala"].id
        assert filha.par_id is None

    # A rota antiga de mudar a aba também leva as duas.
    r = await client.post(
        f"/api/pricing/accounts/{par[PREMIUM].id}/department", json={"department": "eletro"}
    )
    assert r.status_code == 200, r.text
    par = await _par(db, corpo["par_id"])
    assert {linha.segment_id for linha in par.values()} == {cena.raizes["eletro"].id}


async def test_tipo_e_plataforma_do_par_nao_mudam(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cena
):
    auth_as(cena.admin)
    corpo = await _criar_ml(client)
    url = f"/api/pricing/accounts/{corpo['id']}"
    for mudanca in (
        {"listing_type": "ml premium"},
        {"listing_type": None},
        {"platform": "shopee"},
    ):
        r = await client.patch(url, json={**mudanca, "observation": "não grava"})
        assert r.status_code == 400, (mudanca, r.text)
        assert r.json()["detail"]["code"] == "tipo_fixo"
    # O mesmo tipo/plataforma passa (a tela antiga manda o valor de sempre).
    r = await client.patch(url, json={"listing_type": CLASSICO, "platform": "mercadolivre"})
    assert r.status_code == 200, r.text
    par = await _par(db, corpo["par_id"])
    assert {t: (a.listing_type, a.observation) for t, a in par.items()} == {
        CLASSICO: (CLASSICO, None),
        PREMIUM: (PREMIUM, None),
    }


async def test_historico_registra_a_mudanca_nas_duas_linhas(
    db: AsyncSession, client: AsyncClient, cena
):
    """Pelo login de verdade (cookie): é ele que diz ao Histórico quem é."""
    token, _, _ = issue_session_token(sub=cena.admin.open_id, role=cena.admin.role.value)
    client.cookies.set(get_settings().cookie_name, token)
    corpo = await _criar_ml(client)
    par = await _par(db, corpo["par_id"])
    r = await client.patch(f"/api/pricing/accounts/{corpo['id']}", json={"desconto_pct": 3})
    assert r.status_code == 200, r.text
    mudancas = (
        (
            await db.execute(
                select(HistoricoAlteracao).where(
                    HistoricoAlteracao.tabela == "pricing_accounts",
                    HistoricoAlteracao.operacao == "U",
                )
            )
        )
        .scalars()
        .all()
    )
    por_linha = {m.registro_id: m for m in mudancas}
    assert set(por_linha) == {str(par[CLASSICO].id), str(par[PREMIUM].id)}
    for m in por_linha.values():
        assert str(m.ator_id) == str(cena.admin.id)
        assert m.antes["desconto_pct"] is None
        assert m.depois["desconto_pct"] == 3.0
    assert nomes.nome_campo("desconto_pct") == "Desconto (%)"
    assert nomes.nome_campo("custo_total_pct") == "Custo total (%)"


# =========================================================== comissão


async def test_comissao_ml_grava_as_duas_e_so_o_admin(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cena
):
    auth_as(cena.admin)
    corpo = await _criar_ml(client, dept="mala")
    par = await _par(db, corpo["par_id"])
    # O id pode ser de qualquer uma das duas.
    r = await client.put(
        f"/api/pricing/accounts/{par[PREMIUM].id}/comissao-ml",
        json={"classico": 0.145, "premium": 0.2},
    )
    assert r.status_code == 200, r.text
    resposta = r.json()
    assert set(resposta) == {"classico", "premium"}
    assert resposta["classico"]["id"] == str(par[CLASSICO].id)
    assert resposta["premium"]["id"] == str(par[PREMIUM].id)
    assert Decimal(resposta["classico"]["commission"]) == Decimal("0.145")
    assert Decimal(resposta["premium"]["commission"]) == Decimal("0.2")
    par = await _par(db, corpo["par_id"])
    assert (par[CLASSICO].commission, par[PREMIUM].commission) == (
        Decimal("0.1450"),
        Decimal("0.2000"),
    )

    # Null apaga; fora de 0 ≤ x < 1 → 422.
    url = f"/api/pricing/accounts/{corpo['id']}/comissao-ml"
    r = await client.put(url, json={"classico": None, "premium": 0.19})
    assert r.status_code == 200, r.text
    assert r.json()["classico"]["commission"] is None
    for ruim in ({"classico": 1, "premium": 0.1}, {"classico": -0.1, "premium": 0.1}, {}):
        assert (await client.put(url, json=ruim)).status_code == 422, ruim

    # Quem não é ADM: 403, nada muda.
    auth_as(cena.vendedor)
    r = await client.put(url, json={"classico": 0.5, "premium": 0.5})
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "campos_so_admin"
    par = await _par(db, corpo["par_id"])
    assert par[PREMIUM].commission == Decimal("0.1900")

    # Conta sem par → 400 sem_par.
    auth_as(cena.admin)
    r = await client.post(
        "/api/pricing/accounts",
        json={"name": "aguiar", "platform": "shopee", "department": "celular"},
    )
    r = await client.put(
        f"/api/pricing/accounts/{r.json()['id']}/comissao-ml",
        json={"classico": 0.1, "premium": 0.1},
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "sem_par"


# =========================================================== excluir


async def test_excluir_uma_linha_do_par_exclui_as_duas_com_os_catalogos(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cena
):
    auth_as(cena.admin)
    corpo = await _criar_ml(client)
    outra = await _criar_ml(client, "kfa")
    r = await client.post(f"/api/pricing/accounts/{corpo['id']}/catalogo", json={"ativo": True})
    assert r.status_code == 200, r.text
    assert len((await db.execute(select(PricingAccount))).scalars().all()) == 6
    r = await client.delete(f"/api/pricing/accounts/{corpo['id']}")
    assert r.status_code == 204, r.text
    db.expire_all()
    restantes = (await db.execute(select(PricingAccount))).scalars().all()
    assert {str(a.par_id) for a in restantes} == {outra["par_id"]}
    assert len(restantes) == 2


# =========================================================== catálogo


async def test_catalogo_liga_e_desliga_nas_duas_e_a_margem_vale_para_as_duas(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cena
):
    auth_as(cena.admin)
    corpo = await _criar_ml(client)
    par = await _par(db, corpo["par_id"])
    r = await client.post(f"/api/pricing/accounts/{corpo['id']}/catalogo", json={"ativo": True})
    assert r.status_code == 200, r.text
    assert r.json()["ativo"] is True
    filha_id = r.json()["conta_catalogo"]["id"]

    async def filhas() -> dict[str, PricingAccount]:
        rows = (
            (
                await db.execute(
                    select(PricingAccount)
                    .where(PricingAccount.canal == CANAL_CATALOGO)
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        return {next(t for t, b in par.items() if b.id == f.conta_base_id): f for f in rows}

    atuais = await filhas()
    assert set(atuais) == {CLASSICO, PREMIUM}
    assert {t: f.listing_type for t, f in atuais.items()} == {
        CLASSICO: CLASSICO,
        PREMIUM: PREMIUM,
    }

    # Margem do catálogo: grava pela filha do Clássico, vale para as duas.
    r = await client.patch(
        f"/api/pricing/accounts/{filha_id}", json={"margin1": "0.40", "margin2": "0.45"}
    )
    assert r.status_code == 200, r.text
    atuais = await filhas()
    for f in atuais.values():
        assert (f.margin1, f.margin2, f.margin3) == (Decimal("0.4000"), Decimal("0.4500"), None)
    # "Usar as margens da conta" = null nas duas.
    r = await client.patch(f"/api/pricing/accounts/{atuais[PREMIUM].id}", json={"margin1": None})
    assert r.status_code == 200, r.text
    assert {f.margin1 for f in (await filhas()).values()} == {None}

    # Desligar pela irmã desliga as duas.
    r = await client.post(
        f"/api/pricing/accounts/{par[PREMIUM].id}/catalogo", json={"ativo": False}
    )
    assert r.status_code == 200, r.text
    assert r.json()["ativo"] is False
    assert await filhas() == {}


async def test_margem_do_catalogo_sem_filha_na_irma_e_recusada(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cena
):
    auth_as(cena.admin)
    corpo = await _criar_ml(client)
    par = await _par(db, corpo["par_id"])
    # Só o Clássico com catálogo (não deveria acontecer: dado antigo).
    filha = PricingAccount(
        user_id=cena.admin.id,
        name="forpaper",
        platform=PricingPlatform.ML,
        listing_type=CLASSICO,
        segment_id=cena.raizes["celular"].id,
        canal=CANAL_CATALOGO,
        conta_base_id=par[CLASSICO].id,
    )
    db.add(filha)
    await db.commit()
    r = await client.patch(f"/api/pricing/accounts/{filha.id}", json={"margin1": "0.40"})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "catalogo_desencontrado"
    await db.refresh(filha)
    assert filha.margin1 is None


# =========================================================== Ads


async def test_ads_da_conta_e_do_produto_gravam_na_irma(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cena
):
    auth_as(cena.admin)
    corpo = await _criar_ml(client)
    par = await _par(db, corpo["par_id"])
    r = await client.put(
        f"/api/pricing/ads/{corpo['id']}", json={"pode_gastar_pct": 10, "gasto_dia": 50}
    )
    assert r.status_code == 200, r.text
    r = await client.put(
        f"/api/pricing/ads/{par[PREMIUM].id}/produto/{cena.produto.id}",
        json={"pode_gastar_pct": 6},
    )
    assert r.status_code == 200, r.text
    gerais = {
        g.pricing_account_id: (g.pode_gastar_pct, g.gasto_dia, g.atualizado_por)
        for g in (
            await db.execute(select(PricingAdsConta).execution_options(populate_existing=True))
        ).scalars()
    }
    assert gerais == {
        par[CLASSICO].id: (Decimal("10.00"), Decimal("50.00"), cena.admin.id),
        par[PREMIUM].id: (Decimal("10.00"), Decimal("50.00"), cena.admin.id),
    }
    produtos = {
        (p.pricing_account_id, p.pricing_product_id): p.pode_gastar_pct
        for p in (
            await db.execute(select(PricingAdsProduto).execution_options(populate_existing=True))
        ).scalars()
    }
    assert produtos == {
        (par[CLASSICO].id, cena.produto.id): Decimal("6.00"),
        (par[PREMIUM].id, cena.produto.id): Decimal("6.00"),
    }
    # Sem % nem gasto, a linha do produto sai das duas.
    r = await client.put(
        f"/api/pricing/ads/{corpo['id']}/produto/{cena.produto.id}",
        json={"pode_gastar_pct": None},
    )
    assert r.status_code == 200, r.text
    assert (await db.execute(select(PricingAdsProduto))).scalars().all() == []


# =========================================================== Custo total


async def test_mensagem_e_numeros_em_pt_br():
    assert pct_br(Decimal("4.50")) == "4,5"
    assert pct_br(Decimal("10.00")) == "10"
    assert pct_br(Decimal("0")) == "0"
    assert pct_br(Decimal("100")) == "100"
    assert pct_br(Decimal("0.25")) == "0,25"
    # M-94: Desconto + o MAIOR entre Afiliado e Ads (exemplos do pedido, teto 10).
    dez = Decimal("10")
    assert estouro(Decimal("4"), Decimal("6"), Decimal("4"), dez) is None
    assert estouro(Decimal("4"), Decimal("3"), Decimal("7"), dez) is None
    assert estouro(Decimal("4"), Decimal("5"), Decimal("6"), dez) == (
        "Desconto 5% + Ads 6% = 11%. Passa do Custo total (10%). Não gravado."
    )
    assert estouro(Decimal("7"), Decimal("5"), Decimal("2"), dez) == (
        "Afiliado 7% + Desconto 5% = 12%. Passa do Custo total (10%). Não gravado."
    )
    # As duas estouram: mostra a maior.
    assert estouro(Decimal("8"), Decimal("5"), Decimal("6"), dez) == (
        "Afiliado 8% + Desconto 5% = 13%. Passa do Custo total (10%). Não gravado."
    )
    assert estouro(Decimal("6"), Decimal("5"), Decimal("8"), dez) == (
        "Desconto 5% + Ads 8% = 13%. Passa do Custo total (10%). Não gravado."
    )
    # Vazio conta como 0.
    assert estouro(Decimal("4.5"), None, Decimal("0"), Decimal("4")) == (
        "Afiliado 4,5% + Desconto 0% = 4,5%. Passa do Custo total (4%). Não gravado."
    )
    assert estouro(None, Decimal("3"), Decimal("4.5"), Decimal("7")) == (
        "Desconto 3% + Ads 4,5% = 7,5%. Passa do Custo total (7%). Não gravado."
    )
    # Igual ao teto passa; sem teto passa.
    assert estouro(Decimal("4"), None, Decimal("6"), Decimal("10")) is None
    assert estouro(Decimal("10"), None, Decimal("10"), Decimal("10")) is None
    assert estouro(Decimal("99"), Decimal("99"), Decimal("99"), None) is None
    # Ads da soma: o maior % que pode valer (M-78: Geral OU Produto).
    produto, outro = uuid.uuid4(), uuid.uuid4()
    assert EstadoAds().maximo() == Decimal("0")
    # As duas ligadas (dado de antes da M-78): vale só a Geral.
    assert (
        EstadoAds(
            geral_pct=Decimal("5"), por_produto_ligada=True, produtos={produto: Decimal("8")}
        ).maximo()
        == 5
    )
    assert (
        EstadoAds(
            geral_pct=Decimal("5"), por_produto_ligada=False, produtos={produto: Decimal("8")}
        ).maximo()
        == 5
    )
    # Só "Produto": o maior % próprio; a Geral não vale (nem para quem não tem %).
    so_produto = EstadoAds(
        geral_ligada=False,
        geral_pct=Decimal("9"),
        por_produto_ligada=True,
        produtos={produto: Decimal("4"), outro: None},
    )
    assert so_produto.maximo() == 4
    assert replace(so_produto, produtos={}).maximo() == 0
    assert EstadoAds(geral_ligada=False, geral_pct=Decimal("5")).maximo() == 0
    # Ligar uma caixa desliga a outra; desligar não liga a outra.
    assert so_produto.com_geral({"ads_geral_ligada": True}).maximo() == 9
    trocado = so_produto.com_geral({"ads_geral_ligada": True})
    assert (trocado.geral_ligada, trocado.por_produto_ligada) == (True, False)
    volta = trocado.com_geral({"ads_por_produto_ligada": True})
    assert (volta.geral_ligada, volta.por_produto_ligada) == (False, True)
    nenhuma = volta.com_geral({"ads_por_produto_ligada": False})
    assert (nenhuma.geral_ligada, nenhuma.por_produto_ligada, nenhuma.maximo()) == (
        False,
        False,
        0,
    )


async def test_custo_total_aceita_e_recusa_sem_gravar(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cena
):
    """Custo total 10%, Afiliado 4% e Ads 6%: Desconto 5% é recusado (5 + 6 =
    11); Desconto 4% passa (4 + 6 = 10 e 4 + 4 = 8 — M-94: Desconto + o maior
    entre Afiliado e Ads)."""
    auth_as(cena.admin)
    corpo = await _criar_ml(client)
    par = await _par(db, corpo["par_id"])
    url = f"/api/pricing/accounts/{corpo['id']}"
    ads = f"/api/pricing/ads/{corpo['id']}"
    assert (await client.patch(url, json={"custo_total_pct": 10})).status_code == 200
    assert (await client.patch(url, json={"afiliado_pct": 4})).status_code == 200
    assert (await client.put(ads, json={"pode_gastar_pct": 6})).status_code == 200

    r = await client.patch(url, json={"desconto_pct": 5, "observation": "não grava"})
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == {
        "code": "custo_total_estourado",
        "mensagem": "Desconto 5% + Ads 6% = 11%. Passa do Custo total (10%). Não gravado.",
    }
    # Igual ao teto: aceito (antes da M-94 era recusado: 4 + 4 + 6 = 14).
    assert (await client.patch(url, json={"desconto_pct": 4})).status_code == 200
    # Baixar o próprio Custo total para menos que a soma: recusado.
    r = await client.patch(url, json={"custo_total_pct": 9.5})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["mensagem"] == (
        "Desconto 4% + Ads 6% = 10%. Passa do Custo total (9,5%). Não gravado."
    )
    # Baixar o Desconto e o teto juntos (valores novos): aceito.
    r = await client.patch(url, json={"desconto_pct": 3.5, "custo_total_pct": 9.5})
    assert r.status_code == 200, r.text
    par = await _par(db, corpo["par_id"])
    for linha in par.values():
        assert (linha.afiliado_pct, linha.desconto_pct, linha.custo_total_pct) == (
            Decimal("4.00"),
            Decimal("3.50"),
            Decimal("9.50"),
        )
        assert linha.observation is None

    # Ads da conta: % acima do que cabe → recusado, nada muda.
    r = await client.put(ads, json={"pode_gastar_pct": 7, "gasto_dia": 30})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "custo_total_estourado"
    gerais = (
        (await db.execute(select(PricingAdsConta).execution_options(populate_existing=True)))
        .scalars()
        .all()
    )
    assert {(g.pode_gastar_pct, g.gasto_dia) for g in gerais} == {(Decimal("6.00"), None)}
    # Só o gasto por dia não mexe na soma.
    assert (await client.put(ads, json={"gasto_dia": 30})).status_code == 200

    # M-78: Geral OU Produto. Com "Geral" marcado (o padrão) o % do produto
    # fica gravado mas não vale: 9 é aceito.
    produto = f"{ads}/produto/{cena.produto.id}"
    r = await client.put(produto, json={"pode_gastar_pct": 9})
    assert r.status_code == 200, r.text
    assert (r.json()["ads_geral_ligada"], r.json()["ads_por_produto_ligada"]) == (True, False)
    # Trocar para "Produto" faz valer o 9: recusado, nada muda.
    r = await client.put(ads, json={"ads_por_produto_ligada": True})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["mensagem"] == (
        "Desconto 3,5% + Ads 9% = 12,5%. Passa do Custo total (9,5%). Não gravado."
    )
    # Com 6 no produto a troca cabe; a Geral fica desmarcada e o % dela gravado.
    assert (await client.put(produto, json={"pode_gastar_pct": 6})).status_code == 200
    r = await client.put(ads, json={"ads_por_produto_ligada": True})
    assert r.status_code == 200, r.text
    assert (r.json()["ads_geral_ligada"], r.json()["pode_gastar_pct"]) == (False, 6.0)
    assert r.json()["ads_por_produto_ligada"] is True
    # Em "Produto" vale o % próprio: 6,5 não cabe.
    r = await client.put(produto, json={"pode_gastar_pct": 6.5})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["mensagem"] == (
        "Desconto 3,5% + Ads 6,5% = 10%. Passa do Custo total (9,5%). Não gravado."
    )
    # Em "Produto" a Geral não vale; produto sem % próprio = sem Ads (0).
    assert (await client.put(ads, json={"pode_gastar_pct": 5})).status_code == 200
    assert (await client.put(produto, json={"pode_gastar_pct": None})).status_code == 200
    # Sem Ads valendo: 5,5 + Afiliado 4 = 9,5 cabe.
    r = await client.patch(url, json={"desconto_pct": 5.5})
    assert r.status_code == 200, r.text
    # Voltar para "Geral" faz valer o 5 (maior que o Afiliado 4): recusado.
    r = await client.put(ads, json={"ads_geral_ligada": True})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["mensagem"] == (
        "Desconto 5,5% + Ads 5% = 10,5%. Passa do Custo total (9,5%). Não gravado."
    )
    # A dupla do Afiliado também é conferida.
    r = await client.patch(url, json={"afiliado_pct": 4.5})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["mensagem"] == (
        "Afiliado 4,5% + Desconto 5,5% = 10%. Passa do Custo total (9,5%). Não gravado."
    )
    # As duas linhas do par ficaram iguais o tempo todo.
    gerais = {
        g.pricing_account_id: (g.ads_geral_ligada, g.pode_gastar_pct, g.ads_por_produto_ligada)
        for g in (
            await db.execute(select(PricingAdsConta).execution_options(populate_existing=True))
        ).scalars()
    }
    assert gerais[par[CLASSICO].id] == gerais[par[PREMIUM].id] == (False, Decimal("5.00"), True)

    # Conta nova também não nasce acima do teto.
    r = await client.post(
        "/api/pricing/accounts",
        json={
            "name": "nova",
            "platform": "shopee",
            "department": "celular",
            "afiliado_pct": 5,
            "custo_total_pct": 4,
        },
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "custo_total_estourado"


async def test_m94_venda_por_ads_ou_por_afiliado(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cena
):
    """M-94, os exemplos do pedido com Custo total 10: Desconto + o MAIOR entre
    Afiliado e Ads. Vale nas duas linhas do par do ML."""
    auth_as(cena.admin)
    corpo = await _criar_ml(client)
    url = f"/api/pricing/accounts/{corpo['id']}"
    ads = f"/api/pricing/ads/{corpo['id']}"
    assert (await client.patch(url, json={"custo_total_pct": 10})).status_code == 200
    # Afiliado 4, Desconto 6, Ads 4: 4 + 6 = 10 e 4 + 6 = 10 → aceita.
    assert (await client.patch(url, json={"afiliado_pct": 4, "desconto_pct": 6})).status_code == 200
    r = await client.put(ads, json={"pode_gastar_pct": 4})
    assert r.status_code == 200, r.text
    # Afiliado 4, Desconto 3, Ads 7: 4 + 3 = 7 e 7 + 3 = 10 → aceita.
    assert (await client.patch(url, json={"desconto_pct": 3})).status_code == 200
    r = await client.put(ads, json={"pode_gastar_pct": 7})
    assert r.status_code == 200, r.text
    # Afiliado 4, Desconto 5, Ads 6: 6 + 5 = 11 → recusa.
    assert (await client.put(ads, json={"pode_gastar_pct": 6})).status_code == 200
    r = await client.patch(url, json={"desconto_pct": 5})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["mensagem"] == (
        "Desconto 5% + Ads 6% = 11%. Passa do Custo total (10%). Não gravado."
    )
    # Afiliado 7, Desconto 5, Ads 2: 7 + 5 = 12 → recusa, com a versão do
    # Afiliado (pela linha do Premium também).
    assert (await client.put(ads, json={"pode_gastar_pct": 2})).status_code == 200
    assert (await client.patch(url, json={"desconto_pct": 5})).status_code == 200
    par = await _par(db, corpo["par_id"])
    for linha in par.values():
        r = await client.patch(f"/api/pricing/accounts/{linha.id}", json={"afiliado_pct": 7})
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["mensagem"] == (
            "Afiliado 7% + Desconto 5% = 12%. Passa do Custo total (10%). Não gravado."
        )
    par = await _par(db, corpo["par_id"])
    for linha in par.values():
        assert (linha.afiliado_pct, linha.desconto_pct, linha.custo_total_pct) == (
            Decimal("4.00"),
            Decimal("5.00"),
            Decimal("10.00"),
        )
    gerais = (
        (await db.execute(select(PricingAdsConta).execution_options(populate_existing=True)))
        .scalars()
        .all()
    )
    assert {g.pode_gastar_pct for g in gerais} == {Decimal("2.00")}
    assert len(gerais) == 2


async def test_quem_nao_e_admin_ve_o_custo_total_mas_nao_grava(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cena
):
    auth_as(cena.admin)
    corpo = await _criar_ml(client, store_info_id=str(cena.loja.id))
    url = f"/api/pricing/accounts/{corpo['id']}"
    assert (await client.patch(url, json={"custo_total_pct": 12})).status_code == 200
    r = await client.post(f"{url}/catalogo", json={"ativo": True})
    filha_id = r.json()["conta_catalogo"]["id"]
    assert (await client.patch(url, json={"desconto_pct": 2})).status_code == 200

    auth_as(cena.vendedor)
    r = await client.patch(url, json={"custo_total_pct": 50})
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "campos_so_admin"
    # O Desconto (como o Afiliado) é de quem edita Contas; a trava vale igual.
    assert (await client.patch(url, json={"desconto_pct": 5})).status_code == 200
    r = await client.patch(url, json={"desconto_pct": 13})
    assert r.status_code == 422, r.text

    r = await client.get("/api/pricing/accounts", params={"department": "celular"})
    assert r.status_code == 200, r.text
    contas = {c["id"]: c for c in r.json()}
    base = contas[corpo["id"]]
    assert Decimal(base["custo_total_pct"]) == Decimal("12")
    assert Decimal(base["desconto_pct"]) == Decimal("5")
    assert base["par_id"] == corpo["par_id"]
    # Kit e comissão continuam escondidos de quem não é ADM.
    assert base["commission"] is None and base["kit_number"] is None
    # A coluna de catálogo copia o Desconto e o Custo total da base.
    filha = contas[filha_id]
    assert Decimal(filha["custo_total_pct"]) == Decimal("12")
    assert Decimal(filha["desconto_pct"]) == Decimal("5")


# ============================================ revisão (M-53/M-56)


async def test_patch_nao_deixa_linha_ml_de_kit_sem_tipo(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cena
):
    """Conta ML só nasce pelo + Adicionar Conta (com o par). O PATCH não vira
    outra plataforma em ML nem apaga o tipo da ML sem par (nexus); editar os
    outros campos da nexus continua valendo."""
    auth_as(cena.admin)
    r = await client.post(
        "/api/pricing/accounts",
        json={"name": "kfa", "platform": "shopee", "department": "celular"},
    )
    assert r.status_code == 201, r.text
    shopee = f"/api/pricing/accounts/{r.json()['id']}"
    for mudanca in (
        {"platform": "mercadolivre"},
        {"platform": "mercadolivre", "listing_type": CLASSICO},
    ):
        r = await client.patch(shopee, json={**mudanca, "observation": "não grava"})
        assert r.status_code == 400, (mudanca, r.text)
        assert r.json()["detail"]["code"] == "tipo_obrigatorio"
        assert "+ Adicionar Conta" in r.json()["detail"]["message"]
    # A Shopee segue editável.
    assert (await client.patch(shopee, json={"observation": "ok"})).status_code == 200

    nexus = PricingAccount(
        user_id=cena.admin.id,
        name="nexus",
        platform=PricingPlatform.ML,
        segment_id=cena.raizes["mala"].id,
        listing_type=PREMIUM,
    )
    db.add(nexus)
    await db.commit()
    url = f"/api/pricing/accounts/{nexus.id}"
    for mudanca in ({"listing_type": None}, {"listing_type": ""}, {"listing_type": "  "}):
        r = await client.patch(url, json={**mudanca, "observation": "não grava"})
        assert r.status_code == 400, (mudanca, r.text)
        assert r.json()["detail"]["code"] == "tipo_obrigatorio"
    # Os outros campos da nexus (e o mesmo tipo de sempre) continuam valendo.
    r = await client.patch(url, json={"observation": "Mala", "listing_type": PREMIUM})
    assert r.status_code == 200, r.text
    r = await client.patch(url, json={"afiliado_pct": 3})
    assert r.status_code == 200, r.text
    linhas = (
        (
            await db.execute(
                select(PricingAccount)
                .where(PricingAccount.canal != CANAL_CATALOGO)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    assert {(a.name, a.platform, a.listing_type, a.observation) for a in linhas} == {
        ("kfa", PricingPlatform.SHOPEE, None, "ok"),
        ("nexus", PricingPlatform.ML, PREMIUM, "Mala"),
    }


async def test_ads_de_produto_fora_do_departamento_nao_conta_no_custo_total(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cena
):
    """Produto que mudou de departamento deixa a linha órfã no Ads da conta:
    a janela não mostra nem deixa apagar, e a soma do Custo total ignora."""
    auth_as(cena.admin)
    corpo = await _criar_ml(client)
    url = f"/api/pricing/accounts/{corpo['id']}"
    ads = f"/api/pricing/ads/{corpo['id']}"
    # M-78: o % do produto só vale com "Produto" marcado.
    assert (await client.put(ads, json={"ads_por_produto_ligada": True})).status_code == 200
    r = await client.put(f"{ads}/produto/{cena.produto.id}", json={"pode_gastar_pct": 8})
    assert r.status_code == 200, r.text
    r = await client.patch(url, json={"custo_total_pct": 5})
    assert r.status_code == 422, r.text
    assert "Ads 8%" in r.json()["detail"]["mensagem"]

    # O produto vai para a Mala: some da janela do Celular, mas a linha fica.
    produto = await db.get(PricingProduct, cena.produto.id)
    produto.segment_id = cena.folhas["mala"][0].id
    await db.commit()
    r = await client.get("/api/pricing/ads", params={"department": "celular"})
    assert r.status_code == 200, r.text
    assert [p["id"] for p in r.json()["produtos"]] == [str(cena.produto_b.id)]
    assert len((await db.execute(select(PricingAdsProduto))).scalars().all()) == 2

    # A linha órfã não trava mais a conta (nas duas linhas do par).
    r = await client.patch(url, json={"custo_total_pct": 5})
    assert r.status_code == 200, r.text
    # O produto que a janela mostra continua contando.
    r = await client.put(f"{ads}/produto/{cena.produto_b.id}", json={"pode_gastar_pct": 5})
    assert r.status_code == 200, r.text
    r = await client.put(f"{ads}/produto/{cena.produto_b.id}", json={"pode_gastar_pct": 6})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["mensagem"] == (
        "Desconto 0% + Ads 6% = 6%. Passa do Custo total (5%). Não gravado."
    )
    r = await client.put(ads, json={"pode_gastar_pct": 5})
    assert r.status_code == 200, r.text


async def test_custo_total_confere_o_valor_com_as_2_casas_do_banco(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cena
):
    """O banco grava NUMERIC(5,2): a conferência usa o valor arredondado (meio
    para cima), que é o que fica gravado, e a mensagem mostra esse valor."""
    auth_as(cena.admin)
    corpo = await _criar_ml(client)
    url = f"/api/pricing/accounts/{corpo['id']}"
    ads = f"/api/pricing/ads/{corpo['id']}"
    # M-94: Desconto + o maior entre Afiliado e Ads. 6,663 + 3,333 = 9,996
    # passaria de 9,99; gravado é 6,66 + 3,33 = 9,99: cabe.
    r = await client.patch(url, json={"custo_total_pct": 9.99, "afiliado_pct": 3.333})
    assert r.status_code == 200, r.text
    assert (await client.patch(url, json={"desconto_pct": 6.663})).status_code == 200
    r = await client.put(ads, json={"pode_gastar_pct": 3.333})
    assert r.status_code == 200, r.text
    assert r.json()["pode_gastar_pct"] == 3.33
    # 3,335 vira 3,34 (meio para cima): 6,66 + 3,34 = 10 > 9,99.
    r = await client.patch(url, json={"afiliado_pct": 3.335})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["mensagem"] == (
        "Afiliado 3,34% + Desconto 6,66% = 10%. Passa do Custo total (9,99%). Não gravado."
    )
    # O teto também: 9,985 vira 9,99 e cabe; 9,984 vira 9,98 e não.
    assert (await client.patch(url, json={"custo_total_pct": 9.985})).status_code == 200
    r = await client.patch(url, json={"custo_total_pct": 9.984})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["mensagem"].endswith("Passa do Custo total (9,98%). Não gravado.")
    # Ads de produto (com "Produto" marcado, M-78): 3,334 vira 3,33 e cabe;
    # 3,335 vira 3,34 e não.
    assert (await client.put(ads, json={"ads_por_produto_ligada": True})).status_code == 200
    produto = f"{ads}/produto/{cena.produto.id}"
    assert (await client.put(produto, json={"pode_gastar_pct": 3.334})).status_code == 200
    r = await client.put(produto, json={"pode_gastar_pct": 3.335})
    assert r.status_code == 422, r.text
    assert "Ads 3,34%" in r.json()["detail"]["mensagem"]

    par = await _par(db, corpo["par_id"])
    for linha in par.values():
        assert (linha.afiliado_pct, linha.desconto_pct, linha.custo_total_pct) == (
            Decimal("3.33"),
            Decimal("6.66"),
            Decimal("9.99"),
        )
    gerais = (
        (await db.execute(select(PricingAdsConta).execution_options(populate_existing=True)))
        .scalars()
        .all()
    )
    assert {g.pode_gastar_pct for g in gerais} == {Decimal("3.33")}
    produtos = (
        (await db.execute(select(PricingAdsProduto).execution_options(populate_existing=True)))
        .scalars()
        .all()
    )
    assert {p.pode_gastar_pct for p in produtos} == {Decimal("3.33")}


async def test_banco_so_aceita_desconto_e_custo_total_de_0_a_100_e_um_tipo_por_par(
    db: AsyncSession, cena
):
    schema = Base.metadata.schema
    # Os ids antes: o rollback expira os objetos da sessão.
    dono_id, celular_id = cena.admin.id, cena.raizes["celular"].id
    for campo, valor in (("desconto_pct", 101), ("custo_total_pct", -1)):
        with pytest.raises(IntegrityError, match=f"ck_pricing_accounts_{campo}"):
            await db.execute(
                text(
                    f"INSERT INTO {schema}.pricing_accounts (id, user_id, name, platform,"
                    f" segment_id, {campo}) VALUES (gen_random_uuid(), :u, 'x', 'shopee', :s, :v)"
                ),
                {"u": dono_id, "s": celular_id, "v": valor},
            )
        await db.rollback()
    par_id = uuid.uuid4()
    with pytest.raises(IntegrityError, match="uq_pricing_accounts_par_tipo"):
        for _ in range(2):
            await db.execute(
                text(
                    f"INSERT INTO {schema}.pricing_accounts (id, user_id, name, platform,"
                    " segment_id, listing_type, par_id) VALUES (gen_random_uuid(), :u, 'x',"
                    " 'mercadolivre', :s, 'ml classico', :p)"
                ),
                {"u": dono_id, "s": celular_id, "p": par_id},
            )
    await db.rollback()


# =========================================================== migration 0511


async def test_juntar_obs():
    mod = _carregar()
    assert mod.juntar_obs("c68 | Desconto: 0", "Desconto: 0") == "c68 | Desconto: 0"
    assert mod.juntar_obs("Desconto: 0", "c68 | Desconto: 0") == "c68 | Desconto: 0"
    assert mod.juntar_obs(None, "só premium") == "só premium"
    assert mod.juntar_obs("só clássico", "  ") == "só clássico"
    assert mod.juntar_obs("a", "b") == "a | b"
    assert mod.juntar_obs("igual", "igual") == "igual"
    assert mod.juntar_obs(None, "") is None


async def _contas_da_migration(db: AsyncSession, cena) -> SimpleNamespace:
    """Mala: forpaper (Obs, loja vazia no Clássico, Ads só no Clássico,
    catálogo nas duas), marquezini (loja vazia no Premium, slots diferentes);
    nexus só Premium; poofy com a loja arquivada e tudo diferente (oculta);
    Shopee kfa (não é ML)."""
    dono = cena.admin.id
    mala = cena.raizes["mala"].id
    loja_forpaper = StoreInfo(user_id=dono, platform="ml", account_name="forpaper")
    loja_marquezini = StoreInfo(user_id=dono, platform="ml", account_name="marquezini")
    loja_poofy = StoreInfo(
        user_id=dono, platform="ml", account_name="poofy", archived_at=datetime.now(UTC)
    )
    integ = Integration(
        user_id=dono, platform=IntegrationPlatform.ML, name="forpaper", credentials=b"x"
    )
    db.add_all([loja_forpaper, loja_marquezini, loja_poofy, integ])
    await db.flush()
    margens = {"margin1": Decimal("0.44"), "shipping1": Decimal("20"), "afiliado_pct": 4}

    def ml(nome: str, tipo: str, **kw) -> PricingAccount:
        dados = {
            "user_id": dono,
            "name": nome,
            "platform": PricingPlatform.ML,
            "segment_id": mala,
            "listing_type": tipo,
            **margens,
            **kw,
        }
        return PricingAccount(**dados)

    ns = SimpleNamespace(
        fp_c=ml(
            "forpaper",
            CLASSICO,
            commission=Decimal("0.14"),
            observation="c68 | Desconto: 0",
            integration_id=integ.id,
            sort_order=1,
            discount="0",
        ),
        fp_p=ml(
            "forpaper",
            PREMIUM,
            commission=Decimal("0.19"),
            observation="Desconto: 0",
            store_info_id=loja_forpaper.id,
            integration_id=integ.id,
            sort_order=2,
            discount="26.06",
        ),
        mq_c=ml("marquezini", CLASSICO, store_info_id=loja_marquezini.id, observation="Clássico"),
        mq_p=ml(
            "marquezini",
            PREMIUM,
            observation="Premium",
            slot1_segment_id=cena.folhas["mala"][0].id,
        ),
        nexus=ml("nexus", PREMIUM),
        po_c=ml("poofy", CLASSICO, store_info_id=loja_poofy.id, observation="a"),
        po_p=ml("poofy", PREMIUM, margin1=Decimal("0.99"), observation="b"),
        kfa=PricingAccount(
            user_id=dono, name="kfa", platform=PricingPlatform.SHOPEE, segment_id=mala
        ),
        loja_forpaper=loja_forpaper,
        loja_marquezini=loja_marquezini,
    )
    contas = [v for v in vars(ns).values() if isinstance(v, PricingAccount)]
    db.add_all(contas)
    await db.flush()
    for base in (ns.fp_c, ns.fp_p):
        db.add(
            PricingAccount(
                user_id=dono,
                name="forpaper",
                platform=PricingPlatform.ML,
                segment_id=mala,
                listing_type=base.listing_type,
                canal=CANAL_CATALOGO,
                conta_base_id=base.id,
                margin2=Decimal("0.45"),
            )
        )
    db.add(
        PricingAdsConta(
            pricing_account_id=ns.fp_c.id,
            ads_geral_ligada=True,
            pode_gastar_pct=Decimal("10"),
            gasto_dia=None,
            ads_por_produto_ligada=True,
            atualizado_por=dono,
        )
    )
    db.add(
        PricingAdsConta(
            pricing_account_id=ns.po_c.id, pode_gastar_pct=Decimal("3"), atualizado_por=dono
        )
    )
    await db.commit()
    return ns


async def test_migration_pareia_todos_e_harmoniza_so_os_visiveis(db: AsyncSession, cena):
    mod = _carregar()
    ns = await _contas_da_migration(db, cena)
    resumo = await _rodar(db, mod.migrar)
    await db.commit()
    assert resumo == {"pares": 3, "visiveis": 2, "obs": 3, "lojas": 2, "ads": 1}
    contas = [v for v in vars(ns).values() if isinstance(v, PricingAccount)]
    for c in contas:
        await db.refresh(c)
    # Pares: forpaper, marquezini e poofy (oculta também); nexus e Shopee não.
    assert ns.fp_c.par_id == ns.fp_p.par_id is not None
    assert ns.mq_c.par_id == ns.mq_p.par_id is not None
    assert ns.po_c.par_id == ns.po_p.par_id is not None
    assert len({ns.fp_c.par_id, ns.mq_c.par_id, ns.po_c.par_id}) == 3
    assert ns.nexus.par_id is None and ns.kfa.par_id is None

    # forpaper: Obs do Premium já está no Clássico; loja do Premium vai para o
    # Clássico; Ads do Clássico vai para o Premium.
    assert ns.fp_c.observation == ns.fp_p.observation == "c68 | Desconto: 0"
    assert ns.fp_c.store_info_id == ns.fp_p.store_info_id == ns.loja_forpaper.id
    gerais = {
        g.pricing_account_id: g
        for g in (
            await db.execute(select(PricingAdsConta).execution_options(populate_existing=True))
        ).scalars()
    }
    copia = gerais[ns.fp_p.id]
    assert (
        copia.ads_geral_ligada,
        copia.pode_gastar_pct,
        copia.gasto_dia,
        copia.ads_por_produto_ligada,
        copia.atualizado_por,
    ) == (True, Decimal("10.00"), None, True, None)
    # marquezini: Obs juntas (Clássico primeiro); loja do Clássico no Premium;
    # slots NÃO mexem.
    assert ns.mq_c.observation == ns.mq_p.observation == "Clássico | Premium"
    assert ns.mq_p.store_info_id == ns.loja_marquezini.id
    assert (ns.mq_c.slot1_segment_id, ns.mq_p.slot1_segment_id) == (
        None,
        cena.folhas["mala"][0].id,
    )
    # Comissão, ordem e campos antigos ficam como estavam.
    assert (ns.fp_c.commission, ns.fp_p.commission) == (Decimal("0.1400"), Decimal("0.1900"))
    assert (ns.fp_c.sort_order, ns.fp_p.sort_order) == (1, 2)
    assert (ns.fp_c.discount, ns.fp_p.discount) == ("0", "26.06")
    # poofy (loja arquivada): só pareada; nada harmonizado, sem abortar.
    assert (ns.po_c.observation, ns.po_p.observation) == ("a", "b")
    assert ns.po_p.store_info_id is None
    assert ns.po_p.id not in gerais


async def test_migration_aborta_numa_diferenca_nao_prevista(db: AsyncSession, cena):
    mod = _carregar()
    ns = await _contas_da_migration(db, cena)
    ns.mq_p.margin1 = Decimal("0.50")
    ns.mq_p.store_info_id = cena.loja.id  # loja diferente da do Clássico
    filha_premium = (
        await db.execute(select(PricingAccount).where(PricingAccount.conta_base_id == ns.fp_p.id))
    ).scalar_one()
    filha_premium.margin2 = Decimal("0.46")
    await db.commit()
    # Os ids antes: o rollback expira os objetos da sessão.
    fp_p_id, mq_p_id = ns.fp_p.id, ns.mq_p.id
    with pytest.raises(RuntimeError) as erro:
        await _rodar(db, mod.migrar)
    await db.rollback()
    texto = str(erro.value)
    assert "nada foi gravado" in texto
    assert "mala/marquezini (kit 1): margin1 Clássico 0.4400 ≠ Premium 0.5000" in texto
    assert "mala/marquezini (kit 1): loja Clássico" in texto
    assert "mala/forpaper (kit 1): margem 2 do catálogo Clássico 0.4500 ≠ Premium 0.4600" in texto
    # A oculta (poofy) não entra na lista.
    assert "poofy" not in texto
    # Nada gravado (a transação da migration desfaz o pareamento junto).
    contas = (
        (await db.execute(select(PricingAccount).execution_options(populate_existing=True)))
        .scalars()
        .all()
    )
    assert {c.par_id for c in contas} == {None}
    assert fp_p_id not in {
        g.pricing_account_id for g in (await db.execute(select(PricingAdsConta))).scalars()
    }

    # Catálogo ligado só num dos tipos também aborta.
    await db.execute(
        text(
            f"DELETE FROM {Base.metadata.schema}.pricing_accounts"
            " WHERE canal = 'catalogo' AND conta_base_id = :id"
        ),
        {"id": fp_p_id},
    )
    await db.execute(
        text(
            f"UPDATE {Base.metadata.schema}.pricing_accounts"
            " SET margin1 = 0.44, store_info_id = NULL WHERE id = :id"
        ),
        {"id": mq_p_id},
    )
    await db.commit()
    with pytest.raises(RuntimeError, match="mala/forpaper \\(kit 1\\): catálogo ligado só no"):
        await _rodar(db, mod.migrar)
    await db.rollback()


async def _estrutura(db: AsyncSession, schema: str) -> dict[str, list]:
    colunas = (
        await db.execute(
            text(
                "SELECT column_name, data_type, numeric_precision, numeric_scale, is_nullable"
                " FROM information_schema.columns WHERE table_schema = :s"
                " AND table_name = 'pricing_accounts'"
                " AND column_name IN ('par_id', 'desconto_pct', 'custo_total_pct')"
                " ORDER BY column_name"
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
                " AND co.conname IN ('ck_pricing_accounts_desconto_pct',"
                " 'ck_pricing_accounts_custo_total_pct') ORDER BY co.conname"
            ),
            {"s": schema},
        )
    ).all()
    indices = (
        await db.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = :s"
                " AND tablename = 'pricing_accounts' AND indexdef LIKE '%par_id%'"
                " ORDER BY indexname"
            ),
            {"s": schema},
        )
    ).all()
    return {
        "colunas": [tuple(r) for r in colunas],
        "checks": [tuple(r) for r in checks],
        "indices": [(r[0], r[1].replace(f"{schema}.", "")) for r in indices],
    }


async def test_migration_0511_cria_o_que_o_model_declara_e_o_downgrade_desfaz(
    db: AsyncSession,
):
    mod = _carregar()
    assert (mod.revision, mod.down_revision) == (
        "0511_contas_ml_par_custo_total",
        "0510_estoque_entradas_troca_tag",
    )
    schema_model = Base.metadata.schema
    rascunho = f"{schema_model}_mig0511"
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    # Cópias sem constraints das tabelas que a 0511 lê; pricing_accounts sem as
    # três colunas novas.
    for tabela in (
        "pricing_accounts",
        "store_info",
        "integrations",
        "segments",
        "pricing_ads_conta",
        "pricing_ads_produto",
    ):
        await db.execute(
            text(
                f'CREATE TABLE "{rascunho}".{tabela}'
                f' (LIKE "{schema_model}".{tabela} INCLUDING DEFAULTS)'
            )
        )
    await db.execute(
        text(
            f'ALTER TABLE "{rascunho}".pricing_accounts DROP COLUMN par_id,'
            " DROP COLUMN desconto_pct, DROP COLUMN custo_total_pct"
        )
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
        assert len(da_migration["colunas"]) == 3
        assert len(da_migration["checks"]) == 2
        assert len(da_migration["indices"]) == 2
        assert da_migration == await _estrutura(db, schema_model)

        conn = await db.connection()
        await conn.run_sync(_rodar_passo, "downgrade")
        await db.commit()
        assert await _estrutura(db, rascunho) == {"colunas": [], "checks": [], "indices": []}
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()
