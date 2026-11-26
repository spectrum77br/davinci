"""Aba Contas: Margem mínima % da conta (M-85) e o Custo total calculado por ela
(M-86) — migration 0519.

- M-85: um valor por conta (0 a 100, 2 casas, vazio pode). Só o ADM vê e grava:
  quem não é ADM recebe null (lista, grade, resposta de uma conta) e o PATCH com
  ela é recusado. No par do ML vale nas duas linhas; o Catálogo mostra a da base.
- M-86: preenchida, o Custo total = (m − r) × (1 − k) ÷ (1 + m) × 100, inteiro
  para baixo, nunca menor que 0 (m = menor marg das categorias de venda ativas;
  r = Margem mínima; k = comissão, no par a maior). Gravado no custo_total_pct
  e recalculado quando muda a Margem mínima, uma marg, a comissão, o centro de
  custo ou as categorias ativas. Custo total digitado → 409. Abaixo de Afiliado
  + Desconto + Ads já gravados: grava mesmo assim (a tela avisa); a trava do
  M-56 continua para quem mexe em Afiliado, Desconto ou Ads.
Contas FABRICADAS no schema de teste.
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
import math
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
from app.services.pricing.custo_total import calcular_custo_total

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


# =========================================================== a fórmula


@pytest.mark.parametrize(
    ("nome", "margens", "comissoes", "esperado"),
    [
        # Os exemplos do pedido (Margem mínima 25).
        ("Shopee aguiar", ["0.549", "0.356", "0.356", "0.356"], ["0.14"], "6"),
        ("ML atlas", ["0.55", "0.352", "0.352", "0.356"], ["0.11", "0.16"], "6"),
        ("Amazon atlas", ["0.65", "0.425", "0.425", "0.425"], ["0.14"], "10"),
        ("Magalu vortan", ["0.75", "0.45", "0.45", "0.45"], ["0.19"], "11"),
        # Menor marg igual ou abaixo da mínima → 0 (nunca negativo).
        ("menor marg = mínima", ["0.60", "0.25"], ["0.14"], "0"),
        ("menor marg < mínima", ["0.60", "0.098"], ["0.14"], "0"),
        # Vazias não contam; sem nenhuma (ou sem comissão) não calcula.
        ("com vazias", [None, "0.356", None], ["0.14"], "6"),
        ("sem marg", [None, None], ["0.14"], None),
        ("sem comissão", ["0.356"], [None, None], None),
        ("par com uma comissão vazia", ["0.352"], [None, "0.16"], "6"),
    ],
)
def test_formula_exemplos(nome, margens, comissoes, esperado):
    valor = calcular_custo_total(
        [D(m) if m else None for m in margens], D("25"), [D(k) if k else None for k in comissoes]
    )
    assert valor == (D(esperado) if esperado is not None else None), nome


def test_formula_arredonda_para_baixo_sem_erro_de_ponto_flutuante():
    # (0,30 − 0,20) × (1 − 0,22) ÷ 1,30 × 100 = 6 certinho; em float dá
    # 5,999…98 e o "para baixo" erraria para 5.
    assert math.floor((0.30 - 0.20) * (1 - 0.22) / (1 + 0.30) * 100) == 5
    assert calcular_custo_total([D("0.30")], D("20"), [D("0.22")]) == D("6")
    # m = 100%, k = 0: (1 − r) ÷ 2 × 100. r = 88% → 6 exato; um nada acima de 6
    # fica 6; um nada abaixo, 5.
    assert calcular_custo_total([D("1")], D("88"), [D("0")]) == D("6")
    assert calcular_custo_total([D("1")], D("87.9999998"), [D("0")]) == D("6")  # 6,0000001
    assert calcular_custo_total([D("1")], D("88.0000002"), [D("0")]) == D("5")  # 5,9999999
    # Sempre inteiro, de 0 a 100.
    assert calcular_custo_total([D("99.9999")], D("0"), [D("0")]) == D("99")
    assert calcular_custo_total([D("0.5")], D("10"), [D("1")]) == D("0")


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
ATLAS_ML = _margens("0.55", "0.352", "0.352", "0.356")


# =========================================================== M-85 + M-86 na API


async def test_margem_minima_calcula_e_grava_o_custo_total(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cena
):
    conta = await _conta(db, cena, "aguiar", commission=D("0.14"), **AGUIAR)
    url = f"/api/pricing/accounts/{conta.id}"
    auth_as(cena.admin)
    # Vazia: o Custo total é digitado como hoje.
    r = await client.patch(url, json={"custo_total_pct": 15})
    assert r.status_code == 200, r.text
    assert r.json()["margem_minima_pct"] is None

    r = await client.patch(url, json={"margem_minima_pct": 25})
    assert r.status_code == 200, r.text
    assert (D(r.json()["margem_minima_pct"]), D(r.json()["custo_total_pct"])) == (D("25"), D("6"))
    linha = await _ler(db, conta.id)
    assert (linha.margem_minima_pct, linha.custo_total_pct) == (D("25.00"), D("6.00"))

    # Preenchida: o Custo total não se digita (409, nada muda) — nem com outro
    # campo junto.
    for corpo in ({"custo_total_pct": 8}, {"custo_total_pct": None, "afiliado_pct": 1}):
        r = await client.patch(url, json=corpo)
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "custo_total_calculado"
    linha = await _ler(db, conta.id)
    assert (linha.custo_total_pct, linha.afiliado_pct) == (D("6.00"), None)

    # Muda a marg dos grupos → recalcula: menor marg 45% → (0,45 − 0,25) ×
    # 0,86 ÷ 1,45 × 100 = 11,86 → 11.
    r = await client.patch(url, json={"margin2": "0.45", "margin3": "0.45", "margin4": "0.45"})
    assert r.status_code == 200, r.text
    assert D(r.json()["custo_total_pct"]) == D("11")
    # Muda a comissão → recalcula: 30% → 0,2 × 0,7 ÷ 1,45 × 100 = 9,65 → 9.
    r = await client.patch(url, json={"commission": "0.30"})
    assert r.status_code == 200, r.text
    assert D(r.json()["custo_total_pct"]) == D("9")
    # Muda a Margem mínima → recalcula: 40% → 0,05 × 0,7 ÷ 1,45 × 100 = 2,41 → 2.
    r = await client.patch(url, json={"margem_minima_pct": 40})
    assert r.status_code == 200, r.text
    assert D(r.json()["custo_total_pct"]) == D("2")
    # Mínima acima da menor marg → 0.
    r = await client.patch(url, json={"margem_minima_pct": 50})
    assert D(r.json()["custo_total_pct"]) == D("0")
    # Apagar uma marg que não é a menor não muda; apagar todas → vazio (sem teto).
    r = await client.patch(url, json={"margem_minima_pct": 25, "margin1": None})
    assert D(r.json()["custo_total_pct"]) == D("9")
    r = await client.patch(
        url, json={"margin2": None, "margin3": None, "margin4": None}
    )
    assert r.status_code == 200, r.text
    assert r.json()["custo_total_pct"] is None
    assert (await _ler(db, conta.id)).custo_total_pct is None

    # Apagar a Margem mínima: o Custo total fica com o último valor e volta a
    # ser digitado. (marg 60% → 0,35 × 0,7 ÷ 1,6 × 100 = 15,3 → 15.)
    r = await client.patch(url, json={"margin1": "0.6"})
    assert D(r.json()["custo_total_pct"]) == D("15")
    r = await client.patch(url, json={"margem_minima_pct": None})
    assert r.status_code == 200, r.text
    assert (r.json()["margem_minima_pct"], D(r.json()["custo_total_pct"])) == (None, D("15"))
    r = await client.patch(url, json={"custo_total_pct": 12.5})
    assert r.status_code == 200, r.text
    assert D(r.json()["custo_total_pct"]) == D("12.5")
    # Mudar a marg sem Margem mínima não mexe no Custo total digitado.
    r = await client.patch(url, json={"margin1": "0.2"})
    assert D(r.json()["custo_total_pct"]) == D("12.5")
    # Apagar a Margem mínima e digitar o Custo total no mesmo pedido: vale.
    assert (await client.patch(url, json={"margem_minima_pct": 10})).status_code == 200
    r = await client.patch(url, json={"margem_minima_pct": None, "custo_total_pct": 7})
    assert r.status_code == 200, r.text
    assert D(r.json()["custo_total_pct"]) == D("7")


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
    # Não vem em lugar nenhum; o Custo total (calculado) continua indo.
    r = await client.get("/api/pricing/accounts", params={"department": "celular"})
    assert r.status_code == 200, r.text
    (vista,) = r.json()
    assert vista["margem_minima_pct"] is None
    assert D(vista["custo_total_pct"]) == D("6")
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
    assert (linha.margem_minima_pct, linha.custo_total_pct, linha.observation) == (
        D("25.00"),
        D("6.00"),
        "ok",
    )

    # O ADM recebe.
    auth_as(cena.admin)
    r = await client.get("/api/pricing/accounts", params={"department": "celular"})
    assert D(r.json()[0]["margem_minima_pct"]) == D("25")


async def test_par_do_ml_usa_a_maior_comissao_e_grava_nas_duas_e_o_catalogo_segue_a_base(
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
    # Margens do par (as duas linhas) e a Margem mínima: Clássico 11%, Premium
    # 16% → usa 16% → (0,352 − 0,25) × 0,84 ÷ 1,352 × 100 = 6,34 → 6.
    r = await client.patch(url, json={k: str(v) for k, v in ATLAS_ML.items()})
    assert r.status_code == 200, r.text
    r = await client.patch(url, json={"margem_minima_pct": 25})
    assert r.status_code == 200, r.text
    assert D(r.json()["custo_total_pct"]) == D("6")
    par = await _par(db, uuid.UUID(par_id))
    for linha in par.values():
        assert (linha.margem_minima_pct, linha.custo_total_pct) == (D("25.00"), D("6.00"))

    # A janela da comissão (as duas linhas): Premium 30% → 0,102 × 0,7 ÷ 1,352
    # × 100 = 5,28 → 5 nas duas.
    r = await client.put(f"{url}/comissao-ml", json={"classico": 0.11, "premium": 0.30})
    assert r.status_code == 200, r.text
    assert D(r.json()["classico"]["custo_total_pct"]) == D("5")
    assert D(r.json()["premium"]["custo_total_pct"]) == D("5")
    # A maior vale mesmo sendo a do Clássico.
    r = await client.put(f"{url}/comissao-ml", json={"classico": 0.16, "premium": 0.11})
    assert r.status_code == 200, r.text
    par = await _par(db, uuid.UUID(par_id))
    assert {linha.custo_total_pct for linha in par.values()} == {D("6.00")}
    # PATCH de marg pela linha do Premium também grava nas duas.
    r = await client.patch(f"/api/pricing/accounts/{par[PREMIUM].id}", json={"margin2": "0.30"})
    assert r.status_code == 200, r.text
    par = await _par(db, uuid.UUID(par_id))
    # (0,30 − 0,25) × 0,84 ÷ 1,30 × 100 = 3,23 → 3.
    assert {linha.custo_total_pct for linha in par.values()} == {D("3.00")}
    assert {linha.margin2 for linha in par.values()} == {D("0.3000")}
    # Custo total digitado pela outra linha: 409 também.
    r = await client.patch(f"/api/pricing/accounts/{par[PREMIUM].id}", json={"custo_total_pct": 9})
    assert r.status_code == 409, r.text

    # Catálogo: mostra a Margem mínima e o Custo total da base e não se edita.
    r = await client.post(f"{url}/catalogo", json={"ativo": True})
    assert r.status_code == 200, r.text
    filha_id = r.json()["conta_catalogo"]["id"]
    assert D(r.json()["conta_catalogo"]["margem_minima_pct"]) == D("25")
    # Margem própria do catálogo não entra: o Custo total é o da conta.
    r = await client.patch(f"/api/pricing/accounts/{filha_id}", json={"margin2": "0.10"})
    assert r.status_code == 200, r.text
    assert D(r.json()["custo_total_pct"]) == D("3")
    r = await client.patch(f"/api/pricing/accounts/{filha_id}", json={"margem_minima_pct": 10})
    assert r.status_code == 409, r.text
    r = await client.get("/api/pricing/accounts", params={"department": "celular"})
    contas = {c["id"]: c for c in r.json()}
    assert D(contas[filha_id]["margem_minima_pct"]) == D("25")
    assert D(contas[filha_id]["custo_total_pct"]) == D("3")
    # Quem não é ADM: a filha também sai sem a Margem mínima.
    auth_as(cena.vendedor)
    r = await client.get("/api/pricing/accounts", params={"department": "celular"})
    contas = {c["id"]: c for c in r.json()}
    assert contas[filha_id]["margem_minima_pct"] is None
    assert D(contas[filha_id]["custo_total_pct"]) == D("3")


async def test_custo_total_abaixo_da_soma_grava_e_a_trava_continua(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cena
):
    """Item 7 da M-86: Afiliado 4 + Desconto 2 + Ads 3 = 9 com Custo total 10.
    A Margem mínima 25 dá 6 (< 9): grava mesmo assim (quem mexe na Margem
    mínima, na marg ou na comissão não é barrado). A trava do M-56 continua
    para quem mexe em Afiliado, Desconto ou Ads, com o 6."""
    conta = await _conta(db, cena, "aguiar", commission=D("0.14"), **AGUIAR)
    url = f"/api/pricing/accounts/{conta.id}"
    ads = f"/api/pricing/ads/{conta.id}"
    auth_as(cena.admin)
    assert (await client.patch(url, json={"custo_total_pct": 10})).status_code == 200
    assert (await client.patch(url, json={"afiliado_pct": 4, "desconto_pct": 2})).status_code == 200
    assert (await client.put(ads, json={"pode_gastar_pct": 3})).status_code == 200

    r = await client.patch(url, json={"margem_minima_pct": 25})
    assert r.status_code == 200, r.text
    assert D(r.json()["custo_total_pct"]) == D("6")
    # Marg e comissão também gravam abaixo da soma.
    r = await client.patch(url, json={"margin2": "0.30"})
    assert r.status_code == 200, r.text
    # (0,30 − 0,25) × 0,86 ÷ 1,30 × 100 = 3,3 → 3.
    assert D(r.json()["custo_total_pct"]) == D("3")
    r = await client.patch(url, json={"commission": "0.10", "margin2": "0.356"})
    assert r.status_code == 200, r.text
    # (0,106 × 0,9 ÷ 1,356) = 7,03 → 7.
    assert D(r.json()["custo_total_pct"]) == D("7")
    linha = await _ler(db, conta.id)
    assert (linha.afiliado_pct, linha.desconto_pct, linha.custo_total_pct) == (
        D("4.00"),
        D("2.00"),
        D("7.00"),
    )

    # Afiliado/Desconto/Ads: a trava usa o 7 calculado.
    r = await client.patch(url, json={"afiliado_pct": 3})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["mensagem"] == (
        "Afiliado 3% + Desconto 2% + Ads 3% = 8%. Passa do Custo total (7%). Não gravado."
    )
    r = await client.put(ads, json={"pode_gastar_pct": 2.5})
    assert r.status_code == 422, r.text
    # Corrigir para caber: aceito.
    r = await client.patch(url, json={"afiliado_pct": 2})
    assert r.status_code == 200, r.text
    assert (await client.put(ads, json={"pode_gastar_pct": 2})).status_code == 200
    r = await client.put(ads, json={"pode_gastar_pct": 3.5})
    assert r.status_code == 422, r.text
    # Margem mínima junto com o Afiliado no mesmo pedido: a trava usa o Custo
    # total novo (15 → 0,106 × 0,9 ÷ 1,356... com mínima 15: 0,206 × 0,9 ÷ 1,356
    # × 100 = 13,67 → 13).
    r = await client.patch(url, json={"margem_minima_pct": 15, "afiliado_pct": 9})
    assert r.status_code == 200, r.text
    assert D(r.json()["custo_total_pct"]) == D("13")
    r = await client.patch(url, json={"margem_minima_pct": 25, "afiliado_pct": 9})
    assert r.status_code == 422, r.text
    linha = await _ler(db, conta.id)
    assert (linha.margem_minima_pct, linha.afiliado_pct, linha.custo_total_pct) == (
        D("15.00"),
        D("9.00"),
        D("13.00"),
    )


async def test_so_as_categorias_ativas_contam_e_mudar_a_categoria_recalcula(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cena
):
    auth_as(cena.admin)
    # Mala: a marg 5 (5%, da "Queima de estoque" desativada) não aparece na aba
    # e não entra: menor marg 40% → 0,15 × 0,86 ÷ 1,4 × 100 = 9,2 → 9.
    mala = await _conta(
        db,
        cena,
        "kfa",
        dept="mala",
        commission=D("0.14"),
        **_margens("0.52", "0.40", "0.40", "0.40", "0.05"),
    )
    r = await client.patch(f"/api/pricing/accounts/{mala.id}", json={"margem_minima_pct": 25})
    assert r.status_code == 200, r.text
    assert D(r.json()["custo_total_pct"]) == D("9")
    # Celular: a Apple (5ª, ativa) com 9,8% entra → 0.
    cel = await _conta(
        db,
        cena,
        "atlas",
        commission=D("0.14"),
        **_margens("0.439", "0.351", "0.351", "0.351", "0.098"),
    )
    r = await client.patch(f"/api/pricing/accounts/{cel.id}", json={"margem_minima_pct": 25})
    assert D(r.json()["custo_total_pct"]) == D("0")
    # Sem Margem mínima: não é recalculado por nada.
    outra = await _conta(db, cena, "sem", commission=D("0.14"), custo_total_pct=D("8"), **AGUIAR)

    # Desativar a Apple (Cadastros › Segmentos) recalcula as contas do Celular:
    # menor marg 35,1% → 0,101 × 0,86 ÷ 1,351 × 100 = 6,4 → 6.
    apple = cena.folhas["celular"][4]
    r = await client.patch(f"/api/segments/{apple.id}", json={"active": False})
    assert r.status_code == 200, r.text
    assert (await _ler(db, cel.id)).custo_total_pct == D("6.00")
    assert (await _ler(db, outra.id)).custo_total_pct == D("8.00")
    # Reativar volta a 0.
    r = await client.patch(f"/api/segments/{apple.id}", json={"active": True})
    assert r.status_code == 200, r.text
    assert (await _ler(db, cel.id)).custo_total_pct == D("0.00")
    # A Mala não mudou.
    assert (await _ler(db, mala.id)).custo_total_pct == D("9.00")


async def test_criar_e_excluir_categoria_de_venda_recalcula(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cena
):
    """Cadastros › Segmentos: a categoria nova entra no fim do centro (uma
    coluna "marg" a mais na aba Contas) e a excluída sai — o Custo total das
    contas com Margem mínima acompanha."""
    auth_as(cena.admin)
    eletro = Segment(name="Eletro", slug="eletro", sort_order=9)
    db.add(eletro)
    await db.flush()
    for n in range(3):
        db.add(Segment(name=str(n + 1), slug=f"eletro-{n + 1}", sort_order=n, parent_id=eletro.id))
    await db.commit()
    # A marg 4 (5%) está gravada, mas o Eletro só tem 3 categorias: não entra.
    conta = PricingAccount(
        user_id=cena.admin.id,
        name="vortan",
        platform=PricingPlatform.AMAZON,
        segment_id=eletro.id,
        commission=D("0.14"),
        **_margens("0.55", "0.55", "0.55", "0.05"),
    )
    db.add(conta)
    await db.commit()
    r = await client.patch(f"/api/pricing/accounts/{conta.id}", json={"margem_minima_pct": 25})
    # (0,55 − 0,25) × 0,86 ÷ 1,55 × 100 = 16,6 → 16.
    assert D(r.json()["custo_total_pct"]) == D("16")

    r = await client.post("/api/segments", json={"name": "4", "parent_id": str(eletro.id)})
    assert r.status_code == 201, r.text
    nova = r.json()["id"]
    assert (await _ler(db, conta.id)).custo_total_pct == D("0.00"), "a marg 4 (5%) passa a valer"
    r = await client.delete(f"/api/segments/{nova}")
    assert r.status_code == 204, r.text
    assert (await _ler(db, conta.id)).custo_total_pct == D("16.00")


async def test_conta_nova_e_troca_de_centro_de_custo(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cena
):
    auth_as(cena.admin)
    # Conta nova com a Margem mínima: calcula já (Shopee sem marg → vazio).
    r = await client.post(
        "/api/pricing/accounts",
        json={
            "name": "nova",
            "platform": "shopee",
            "department": "celular",
            "commission": "0.14",
            "margin2": "0.356",
            "margem_minima_pct": 25,
            "afiliado_pct": 9,
        },
    )
    assert r.status_code == 201, r.text
    assert D(r.json()["custo_total_pct"]) == D("6")
    r = await client.post(
        "/api/pricing/accounts",
        json={
            "name": "outra",
            "platform": "shopee",
            "department": "celular",
            "margem_minima_pct": 25,
            "custo_total_pct": 6,
        },
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "custo_total_calculado"
    # ML: nasce com as comissões usuais (11% e 16%) e a maior entra.
    r = await client.post(
        "/api/pricing/accounts",
        json={
            "name": "ml",
            "platform": "mercadolivre",
            "department": "celular",
            "margin1": "0.352",
            "margem_minima_pct": 25,
        },
    )
    assert r.status_code == 201, r.text
    assert D(r.json()["custo_total_pct"]) == D("6")

    # Trocar de centro de custo: na Mala só as 4 ativas contam.
    conta = await _conta(
        db,
        cena,
        "kfa",
        commission=D("0.14"),
        margem_minima_pct=D("25"),
        **_margens("0.52", "0.40", "0.40", "0.40", "0.05"),
    )
    r = await client.post(
        f"/api/pricing/accounts/{conta.id}/department", json={"department": "mala"}
    )
    assert r.status_code == 200, r.text
    assert D(r.json()["custo_total_pct"]) == D("9")
    r = await client.patch(
        f"/api/pricing/accounts/{conta.id}", json={"department": "celular"}
    )
    assert r.status_code == 200, r.text
    assert D(r.json()["custo_total_pct"]) == D("0")


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
