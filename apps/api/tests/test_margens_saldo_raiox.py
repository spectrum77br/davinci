"""GET /api/margens/marketplace/saldo-detalhe/{pedido} — raio-X do saldo.

A "foto" que explica os dois números da aba Margem: Saldo Bling (valor base −
frete − taxa do pedido de venda) e Saldo Plataforma (repasse do marketplace:
bruto − taxas − frete ± ajustes = líquido). Lê SÓ o snapshot verificar_margem,
então os valores têm que bater com a listagem da tela.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PricingAccount, PricingPlatform, Segment

pytestmark = pytest.mark.asyncio

_ROTA = "/api/margens/marketplace/saldo-detalhe"


def _perms() -> dict:
    return {"margem": {"view": True, "edit": False, "delete": False}}


async def _seed_item(db: AsyncSession, **cols: object) -> None:
    """Uma linha-item no snapshot; colunas não passadas ficam NULL."""
    cols.setdefault("bling_order_item_id", str(uuid.uuid4()))
    names = ", ".join(cols)
    binds = ", ".join(f":{c}" for c in cols)
    await db.execute(
        text(f"INSERT INTO verificar_margem ({names}) VALUES ({binds})"),  # noqa: S608
        cols,
    )
    await db.commit()


async def test_saldo_detalhe_requer_permissao_de_view(client, make_user, auth_as):
    user = await make_user(permissions={})
    auth_as(user)

    response = await client.get(f"{_ROTA}/291675")

    assert response.status_code == 403


async def test_saldo_detalhe_404_sem_snapshot(client, make_user, auth_as):
    user = await make_user(permissions=_perms())
    auth_as(user)

    response = await client.get(f"{_ROTA}/999999")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "pedido_nao_encontrado"


async def test_saldo_detalhe_monta_os_dois_lados(
    client, db: AsyncSession, make_user, auth_as
):
    """Caso real (291675): Bling sem taxa/frete lançados × repasse com taxas —
    o raio-X tem que abrir os componentes dos dois lados e a diferença."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_item(
        db,
        pedido_bling="401001",
        pedido_marketplace="MKT-401001",
        sku="sku-a",
        produto="Notebook X",
        quantidade=1,
        item_proportion=1,
        plataforma_bling="mercadolivre",
        loja_nome="Loja ML",
        situacao_nome="Em aberto",
        bling_valorbase_item=Decimal("2831.41"),
        bling_custofrete_item=0,
        bling_taxacomissao_item=0,
        bling_valorbase_pedido=Decimal("2831.41"),
        bling_custofrete_pedido=0,
        bling_taxacomissao_pedido=0,
        marketplace_valor_bruto_pedido=Decimal("2799.42"),
        marketplace_taxas_pedido=Decimal("492.55"),
        marketplace_frete_pedido=Decimal("22.55"),
        marketplace_desconto_pedido=0,
        marketplace_liquido_base_margem_pedido=Decimal("2284.32"),
        marketplace_liquido_base_margem_item=Decimal("2284.32"),
        financeiro_atualizado_em=datetime(2026, 8, 20, 10, 0, tzinfo=UTC),
    )

    response = await client.get(f"{_ROTA}/401001")

    assert response.status_code == 200
    body = response.json()
    assert body["pedido_bling"] == "401001"
    assert body["plataforma"] == "mercadolivre"
    assert body["conta"] == "Loja ML"
    # Lado Bling: 2831.41 − 0 − 0.
    assert body["bling_valorbase"] == pytest.approx(2831.41, abs=0.01)
    assert body["saldo_bling"] == pytest.approx(2831.41, abs=0.01)
    # Lado plataforma: componentes + líquido.
    assert body["mp_valor_bruto"] == pytest.approx(2799.42, abs=0.01)
    assert body["mp_taxas"] == pytest.approx(492.55, abs=0.01)
    assert body["mp_frete"] == pytest.approx(22.55, abs=0.01)
    assert body["mp_liquido"] == pytest.approx(2284.32, abs=0.01)
    assert body["saldo_plataforma"] == pytest.approx(2284.32, abs=0.01)
    assert body["mp_atualizado_em"].startswith("2026-08-20")
    assert body["projecao_amazon"] is False
    assert body["diferenca"] == pytest.approx(547.09, abs=0.01)
    # Por item: mesmo cálculo da listagem (_SALDO_BLING_SQL/_SALDO_PLATAFORMA_SQL).
    assert len(body["itens"]) == 1
    assert body["itens"][0]["saldo_bling"] == pytest.approx(2831.41, abs=0.01)
    assert body["itens"][0]["saldo_plataforma"] == pytest.approx(2284.32, abs=0.01)


async def test_saldo_detalhe_pack_rateia_por_item(
    client, db: AsyncSession, make_user, auth_as
):
    """Pack com 2 itens: totais vêm das colunas _pedido; cada item traz o rateio
    (item_proportion) com os mesmos saldos por item da listagem."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    comum = {
        "pedido_bling": "401002",
        "plataforma_bling": "shopee",
        "bling_valorbase_pedido": 100,
        "bling_custofrete_pedido": 10,
        "bling_taxacomissao_pedido": 20,
        "marketplace_valor_bruto_pedido": 110,
        "marketplace_taxas_pedido": 15,
        "marketplace_frete_pedido": 5,
        "marketplace_liquido_base_margem_pedido": 90,
    }
    await _seed_item(
        db,
        **comum,
        sku="sku-a",
        quantidade=1,
        item_proportion=Decimal("0.6"),
        bling_valorbase_item=60,
        bling_custofrete_item=6,
        bling_taxacomissao_item=12,
        marketplace_liquido_base_margem_item=54,
    )
    await _seed_item(
        db,
        **comum,
        sku="sku-b",
        quantidade=2,
        item_proportion=Decimal("0.4"),
        bling_valorbase_item=40,
        bling_custofrete_item=4,
        bling_taxacomissao_item=8,
        marketplace_liquido_base_margem_item=36,
    )

    response = await client.get(f"{_ROTA}/401002")

    assert response.status_code == 200
    body = response.json()
    # Totais do pedido: 100 − 10 − 20 = 70 × líquido 90 → diferença −20.
    assert body["saldo_bling"] == pytest.approx(70, abs=0.01)
    assert body["saldo_plataforma"] == pytest.approx(90, abs=0.01)
    assert body["diferenca"] == pytest.approx(-20, abs=0.01)
    # Rateio por item, ordenado por SKU.
    assert [i["sku"] for i in body["itens"]] == ["sku-a", "sku-b"]
    a, b = body["itens"]
    assert a["proporcao"] == pytest.approx(0.6, abs=0.001)
    assert a["saldo_bling"] == pytest.approx(42, abs=0.01)
    assert a["saldo_plataforma"] == pytest.approx(54, abs=0.01)
    assert b["proporcao"] == pytest.approx(0.4, abs=0.001)
    assert b["saldo_bling"] == pytest.approx(28, abs=0.01)
    assert b["saldo_plataforma"] == pytest.approx(36, abs=0.01)


async def test_saldo_detalhe_projecao_amazon(
    client, db: AsyncSession, make_user, auth_as
):
    """Amazon pré-liquidação (líquido real NULL): o lado plataforma fica EM
    BRANCO mesmo com todos os insumos da antiga projeção presentes (conta
    pricing com comissão + frete projetado). A projeção ≈ morreu em 01/09
    ("retirar a projeção... o saldo efetivo deixe sempre em branco"): sem
    líquido REAL não se exibe nem compara nada — commit 02b9808 trocou
    _SALDO_PLATAFORMA_SQL por marketplace_liquido_base_margem_item puro."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    seg = Segment(
        slug=f"seg-{uuid.uuid4().hex[:6]}", name="Seg", parent_id=None, sort_order=0
    )
    db.add(seg)
    await db.flush()
    acc = PricingAccount(
        user_id=user.id,
        name="conta-amz",
        platform=PricingPlatform.AMAZON,
        segment_id=seg.id,
        commission=Decimal("0.15"),
    )
    db.add(acc)
    await db.commit()
    await db.refresh(acc)
    await _seed_item(
        db,
        pedido_bling="401003",
        sku="sku-a",
        quantidade=1,
        item_proportion=1,
        plataforma_bling="amazon",
        pricing_account_id=str(acc.id),
        bling_valorbase_item=100,
        bling_valorbase_pedido=100,
        bling_custofrete_pedido=0,
        bling_taxacomissao_pedido=0,
        frete_projetado_item=20,
    )

    response = await client.get(f"{_ROTA}/401003")

    assert response.status_code == 200
    body = response.json()
    assert body["mp_liquido"] is None
    assert body["saldo_plataforma"] is None
    assert body["projecao_amazon"] is False
    assert body["proj_frete_projetado"] is None
    assert body["proj_comissao_frac"] is None
    assert body["diferenca"] is None
    assert body["itens"][0]["saldo_plataforma"] is None


async def test_saldo_detalhe_aguardando_plataforma(
    client, db: AsyncSession, make_user, auth_as
):
    """Sem financeiro do marketplace (e sem projeção): o lado plataforma fica
    vazio — sem inventar número — e a diferença não é calculada.

    Fixture usa 'magalu' (fora da lista de projeção de _SALDO_PLATAFORMA_SQL:
    não há repasse de plataforma a projetar). Era 'tiktok', mas desde 31/08 a
    projeção cobre amazon/tiktok/shopee/ml — com tiktok o lado plataforma
    passou a vir preenchido (≈100) e este teste quebrou; o caminho projetado
    do raio-X já é coberto pelo teste anterior (amazon)."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_item(
        db,
        pedido_bling="401004",
        sku="sku-a",
        quantidade=1,
        item_proportion=1,
        plataforma_bling="magalu",
        bling_valorbase_item=100,
        bling_custofrete_item=5,
        bling_taxacomissao_item=10,
        bling_valorbase_pedido=100,
        bling_custofrete_pedido=5,
        bling_taxacomissao_pedido=10,
    )

    response = await client.get(f"{_ROTA}/401004")

    assert response.status_code == 200
    body = response.json()
    assert body["saldo_bling"] == pytest.approx(85, abs=0.01)
    assert body["saldo_plataforma"] is None
    assert body["projecao_amazon"] is False
    assert body["diferenca"] is None
    assert body["mp_valor_bruto"] is None
