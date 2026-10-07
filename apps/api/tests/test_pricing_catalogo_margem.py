"""Margem própria do Catálogo ML (07/10/2026).

Eduardo: "para o catálogo a gente tem que poder colocar a margem própria
dele também". A coluna de catálogo ("filha") guarda margin1..5 próprias: a
margem do tipo T é a da filha quando preenchida, senão a da conta de kit.
Comissão e frete continuam sempre os da base. Só as margens se editam na
filha (o resto continua 409). Desligar o catálogo apaga a filha: ao religar,
a filha nova nasce sem margem própria. O ML é SIMULADO (MLFalso).
"""

# ruff: noqa: F811  (fixtures importadas de test_pricing_catalogo_ml)
from __future__ import annotations

import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    HistoricoAlteracao,
    PricingAccount,
    PricingPlatform,
    StoreInfo,
    User,
    UserRole,
    UserStatus,
)
from app.security.jwt import issue_session_token
from app.services.pricing.calc import calculate
from tests.test_pricing_catalogo_ml import (  # noqa: F401 — fixtures
    PERM_FULL,
    MLFalso,
    _conta,
    _integracao_ml,
    _ligar,
    _produto,
    _push,
    _segmentos,
    cenario,
    dono,
    ml_falso,
)


async def _margem(client: AsyncClient, conta_id, **margens):
    return await client.patch(f"/api/pricing/accounts/{conta_id}", json=margens)


async def _celulas(client: AsyncClient) -> dict[tuple[str, str], dict[str, Any]]:
    r = await client.get("/api/pricing/grid", params={"department": "celular"})
    assert r.status_code == 200, r.text
    return {
        (c["pricing_account_id"], c["pricing_product_id"]): c for c in r.json()["cells"]
    }


def _preco(celulas, conta_id, produto) -> Decimal | None:
    p = celulas[(str(conta_id), str(produto.id))]["price"]
    return None if p is None else Decimal(p)


# =========================================================== cálculo


def test_margem_propria_por_tipo_e_comissao_frete_da_base():
    base = _conta(
        commission=Decimal("0.10"),
        margin1=Decimal("0.20"), shipping1=Decimal("5"),
        margin3=Decimal("0.30"), shipping3=Decimal("10"),
    )
    # Comissão e frete gravados na filha (não devia acontecer) são ignorados.
    filha = _conta(
        canal="catalogo", commission=Decimal("0.50"),
        margin1=Decimal("0.40"), shipping1=Decimal("99"),
    )
    prod = _produto(preco_catalogo=Decimal("55"))

    out = calculate(filha, prod, None, 1, conta_base=base)
    # (55 × 1.4 + 5) / 0.9 = 91.11 → 91
    assert (out.source, out.price) == ("computed", Decimal("91"))
    assert out.inputs["margin"] == "0.40"
    assert out.inputs["commission"] == "0.10"
    assert out.inputs["shipping"] == "5"
    assert out.inputs["margem_propria"] is True

    # Tipo 3 sem margem própria: a da base. (55 × 1.3 + 10) / 0.9 = 90.56 → 91
    out = calculate(filha, prod, None, 3, conta_base=base)
    assert out.inputs["margin"] == "0.30"
    assert out.inputs["margem_propria"] is False
    assert out.price == Decimal("91")

    # Margem própria 0 é margem (não "vazio"): (55 + 5) / 0.9 = 66.67 → 67
    filha.margin1 = Decimal("0")
    assert calculate(filha, prod, None, 1, conta_base=base).price == Decimal("67")

    # A conta de kit não muda: Kit 1 = 40 → (40 × 1.2 + 5) / 0.9 = 58.89 → 59
    assert calculate(base, prod, None, 1).price == Decimal("59")


def test_margem_propria_nao_tira_o_sem_preco_de_catalogo():
    base = _conta(margin1=Decimal("0.20"))
    filha = _conta(canal="catalogo", margin1=Decimal("0.40"))
    out = calculate(filha, _produto(preco_catalogo=None), None, 1, conta_base=base)
    assert (out.price, out.detail) == (None, "sem_preco_catalogo")


def test_margem_propria_sem_margem_na_base():
    """Base sem margem no tipo: com a própria calcula; sem ela, falta margem."""
    base = _conta(shipping2=Decimal("5"))
    filha = _conta(canal="catalogo")
    prod = _produto(preco_catalogo=Decimal("55"))
    assert calculate(filha, prod, None, 2, conta_base=base).source == "missing_inputs"
    filha.margin2 = Decimal("0.20")
    # (55 × 1.2 + 5) / 0.9 = 78.89 → 79
    assert calculate(filha, prod, None, 2, conta_base=base).price == Decimal("79")


# =========================================================== PATCH na filha


@pytest.mark.asyncio
async def test_patch_so_aceita_margem_na_filha(
    db: AsyncSession, client: AsyncClient, dono: User, cenario, auth_as: Callable,
):
    auth_as(dono)
    base = cenario["base"]
    filha_id = (await _ligar(client, base.id)).json()["conta_catalogo"]["id"]
    vazio = {str(i): None for i in range(1, 6)}

    r = await _margem(client, filha_id, margin1="0.4000", margin4="0.1250")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["margens_catalogo"] == {**vazio, "1": "0.4000", "4": "0.1250"}
    # Os campos de sempre continuam com os números da base.
    assert Decimal(out["margin1"]) == Decimal("0.20")
    assert out["margin4"] is None
    assert Decimal(out["commission"]) == Decimal("0.10")
    assert out["canal"] == "catalogo"
    linha = await db.get(PricingAccount, uuid.UUID(filha_id))
    await db.refresh(linha)
    assert (linha.margin1, linha.margin4) == (Decimal("0.4"), Decimal("0.125"))
    assert linha.commission is None and linha.shipping1 is None

    # /accounts e /grid trazem as próprias na filha; a base não tem o campo.
    contas = {c["id"]: c for c in (await client.get("/api/pricing/accounts")).json()}
    assert contas[filha_id]["margens_catalogo"]["1"] == "0.4000"
    assert Decimal(contas[filha_id]["margin1"]) == Decimal("0.20")
    assert contas[str(base.id)]["margens_catalogo"] is None
    grade = (await client.get("/api/pricing/grid", params={"department": "celular"})).json()
    assert {a["id"]: a for a in grade["accounts"]}[filha_id]["margens_catalogo"]["4"] == "0.1250"

    # null volta a usar a da base.
    r = await _margem(client, filha_id, margin4=None)
    assert r.status_code == 200, r.text
    assert r.json()["margens_catalogo"] == {**vazio, "1": "0.4000"}

    # Qualquer outro campo continua 409 — e nada muda, nem a margem junto.
    for corpo in (
        {"commission": "0.5"},
        {"shipping1": "9"},
        {"name": "outro"},
        {"discount": "10%"},
        {"kit_number": 2},
        {"margin1": "0.9", "commission": "0.5"},
        {"margin1": "0.9", "shipping1": None},
    ):
        r = await client.patch(f"/api/pricing/accounts/{filha_id}", json=corpo)
        assert r.status_code == 409, (corpo, r.text)
        assert r.json()["detail"]["code"] == "conta_catalogo_herda_da_base"
    r = await client.post(
        f"/api/pricing/accounts/{filha_id}/department", json={"department": "mala"}
    )
    assert r.status_code == 409
    await db.refresh(linha)
    assert linha.margin1 == Decimal("0.4")
    assert linha.commission is None and linha.shipping1 is None and linha.name == "counhago"

    # Corpo vazio: nada a fazer.
    r = await client.patch(f"/api/pricing/accounts/{filha_id}", json={})
    assert r.status_code == 200
    assert r.json()["margens_catalogo"]["1"] == "0.4000"


@pytest.mark.asyncio
async def test_mudar_a_base_nao_apaga_a_margem_propria(
    db: AsyncSession, client: AsyncClient, dono: User, cenario, auth_as: Callable,
):
    auth_as(dono)
    base = cenario["base"]
    filha_id = (await _ligar(client, base.id)).json()["conta_catalogo"]["id"]
    assert (await _margem(client, filha_id, margin1="0.40")).status_code == 200

    r = await client.patch(
        f"/api/pricing/accounts/{base.id}",
        json={"name": "counhago 2", "listing_type": "ml premium", "sort_order": 9,
              "margin1": "0.25", "commission": "0.12"},
    )
    assert r.status_code == 200, r.text
    r = await client.post(
        f"/api/pricing/accounts/{base.id}/department", json={"department": "mala"}
    )
    assert r.status_code == 200, r.text

    linha = await db.get(PricingAccount, uuid.UUID(filha_id))
    await db.refresh(linha)
    assert (linha.name, linha.listing_type, linha.sort_order) == ("counhago 2", "ml premium", 9)
    assert linha.segment_id == cenario["seg"]["mala"].id
    assert linha.margin1 == Decimal("0.40")
    assert linha.commission is None
    contas = {c["id"]: c for c in (await client.get("/api/pricing/accounts")).json()}
    assert contas[filha_id]["margens_catalogo"]["1"] == "0.4000"
    assert Decimal(contas[filha_id]["margin1"]) == Decimal("0.25")


# =========================================================== /grid e envio


@pytest.mark.asyncio
async def test_grid_e_envio_usam_a_margem_propria(
    db: AsyncSession, client: AsyncClient, dono: User, cenario, auth_as: Callable,
    ml_falso: MLFalso,
):
    auth_as(dono)
    base = cenario["base"]
    a003, dg052 = cenario["a003"], cenario["dg052"]
    filha_id = (await _ligar(client, base.id)).json()["conta_catalogo"]["id"]

    cel = await _celulas(client)
    # Sem margem própria: a da base. (55 × 1.2 + 5) / 0.9 = 79
    assert _preco(cel, filha_id, a003) == Decimal("79")
    assert _preco(cel, base.id, a003) == Decimal("59")

    # Margem própria no tipo 1 (a003 é acessório) e no tipo 3 (dg052).
    assert (await _margem(client, filha_id, margin1="0.40", margin3="0.50")).status_code == 200
    cel = await _celulas(client)
    # (55 × 1.4 + 5) / 0.9 = 91.11 → 91
    assert _preco(cel, filha_id, a003) == Decimal("91")
    assert cel[(filha_id, str(a003.id))]["source"] == "computed"
    # dg052 continua sem preço de catálogo; a coluna de kit não muda.
    assert _preco(cel, filha_id, dg052) is None
    assert cel[(filha_id, str(dg052.id))]["catalogo"]["bloqueio"] == "sincronizado"
    assert _preco(cel, base.id, a003) == Decimal("59")

    # Comissão e frete continuam os da base: frete 5 → 14 e comissão 10% → 20%.
    r = await client.patch(
        f"/api/pricing/accounts/{base.id}", json={"shipping1": "14", "commission": "0.20"}
    )
    assert r.status_code == 200, r.text
    cel = await _celulas(client)
    # (55 × 1.4 + 14) / 0.8 = 113.75 → 114
    assert _preco(cel, filha_id, a003) == Decimal("114")

    # Envio usa o preço com a margem própria, só no anúncio de catálogo.
    for mlb in ("MLB100", "MLB101", "MLB202"):
        ml_falso.item(mlb)
    ml_falso.item("MLB200", catalog_listing=True)
    ml_falso.item("MLB201", catalog_listing=True, listing_type_id="gold_pro")
    out = await _push(client, filha_id, a003.id)
    assert out["ok"] is True, out
    assert Decimal(out["price"]) == Decimal("114")
    assert ml_falso.puts_de_preco() == [("MLB200", {"price": 114})]

    # Apagar a margem própria volta ao preço com a margem da base.
    assert (await _margem(client, filha_id, margin1=None)).status_code == 200
    cel = await _celulas(client)
    # (55 × 1.2 + 14) / 0.8 = 100
    assert _preco(cel, filha_id, a003) == Decimal("100")
    ml_falso.chamadas.clear()
    out = await _push(client, filha_id, a003.id)
    assert out["ok"] is True, out
    assert ml_falso.puts_de_preco() == [("MLB200", {"price": 100})]


@pytest.mark.asyncio
async def test_desligar_e_religar_nasce_sem_margem_propria(
    db: AsyncSession, client: AsyncClient, dono: User, cenario, auth_as: Callable,
):
    """Desligar apaga a filha e as margens próprias dela; a filha nova nasce
    sem nenhuma (decisão documentada em definir_catalogo_da_conta)."""
    auth_as(dono)
    base = cenario["base"]
    filha_id = (await _ligar(client, base.id)).json()["conta_catalogo"]["id"]
    assert (await _margem(client, filha_id, margin1="0.40")).status_code == 200
    assert (await _ligar(client, base.id, False)).status_code == 200
    r = await _ligar(client, base.id)
    assert r.status_code == 200
    nova = r.json()["conta_catalogo"]
    assert nova["id"] != filha_id
    assert nova["margens_catalogo"] == {str(i): None for i in range(1, 6)}
    assert _preco(await _celulas(client), nova["id"], cenario["a003"]) == Decimal("79")


# =========================================================== permissão e equipe


@pytest.mark.asyncio
async def test_margem_propria_permissao_e_equipe(
    db: AsyncSession, client: AsyncClient, dono: User, auth_as: Callable,
):
    seg = await _segmentos(db)
    contas = {}
    for equipe, nome in ((1, "loja equipe 1"), (2, "loja equipe 2")):
        integ = await _integracao_ml(db, dono, nome)
        db.add(StoreInfo(user_id=dono.id, platform="ml", account_name=nome,
                         integration_id=integ.id, sales_team=equipe))
        conta = PricingAccount(user_id=dono.id, name=nome, platform=PricingPlatform.ML,
                               listing_type="ml classico", segment_id=seg["celular"].id,
                               commission=Decimal("0.1"), margin1=Decimal("0.2"),
                               integration_id=integ.id)
        db.add(conta)
        contas[equipe] = conta
    await db.commit()
    for c in contas.values():
        await db.refresh(c)

    auth_as(dono)
    filhas = {
        eq: (await _ligar(client, c.id)).json()["conta_catalogo"]["id"]
        for eq, c in contas.items()
    }

    # Sem permissão de editar contas → 403.
    leitor = User(
        open_id=f"email:l-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"l-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions={"tabela_precos_contas": {"view": True}},
    )
    membro = User(
        open_id=f"email:eq-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"eq-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions=PERM_FULL,
        sales_teams=[1],
    )
    db.add_all([leitor, membro])
    await db.commit()
    auth_as(leitor)
    assert (await _margem(client, filhas[1], margin1="0.4")).status_code == 403

    # Equipe 1: a coluna de catálogo da equipe 2 não existe para ela.
    auth_as(membro)
    r = await _margem(client, filhas[2], margin1="0.4")
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["code"] == "account_not_found"
    r = await _margem(client, filhas[1], margin1="0.4")
    assert r.status_code == 200, r.text

    linhas = {
        eq: await db.get(PricingAccount, uuid.UUID(fid)) for eq, fid in filhas.items()
    }
    for linha in linhas.values():
        await db.refresh(linha)
    assert linhas[1].margin1 == Decimal("0.4")
    assert linhas[2].margin1 is None


# =========================================================== histórico


@pytest.mark.asyncio
async def test_margem_propria_entra_no_historico(
    db: AsyncSession, client: AsyncClient, dono: User, cenario,
):
    """Pelo login de verdade (cookie): é ele que diz ao Histórico quem é."""
    token, _, _ = issue_session_token(sub=dono.open_id, role=dono.role.value)
    client.cookies.set(get_settings().cookie_name, token)
    dono_id = dono.id
    filha_id = (await _ligar(client, cenario["base"].id)).json()["conta_catalogo"]["id"]
    assert (await _margem(client, filha_id, margin1="0.40")).status_code == 200

    q = select(HistoricoAlteracao).where(
        HistoricoAlteracao.tabela == "pricing_accounts",
        HistoricoAlteracao.registro_id == filha_id,
        HistoricoAlteracao.operacao == "U",
    )
    [mudou] = (await db.execute(q.execution_options(populate_existing=True))).scalars().all()
    assert str(mudou.ator_id) == str(dono_id)
    assert mudou.antes["margin1"] is None
    assert mudou.depois["margin1"] == 0.4
