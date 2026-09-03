"""Saldo Efetivo digitado NA MÃO na aba Margem (Eduardo, 03/09).

"coloque a opção de preencher manualmente tbm, esta automatico mas e bom dar
pra preencher na mao tbm" — em ML/Shopee/TikTok o Efetivo fica "—" até o
líquido REAL da plataforma sincronizar. O PUT /saldo-manual guarda o valor
digitado (tabela margem_saldo_manual, NADA vai pro Bling); a listagem o usa
via COALESCE(real, manual) e o real VENCE quando chega (regra de 01/09).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder

pytestmark = pytest.mark.asyncio


def _margem_permissions() -> dict:
    return {"margem": {"view": True, "edit": True, "delete": False}}


async def _seed_item(
    db: AsyncSession,
    *,
    pedido: str,
    bling_id: int,
    plataforma: str = "shopee",
    liquido: float | None = None,
    valorbase: float = 200.0,
    custo: float | None = 100.0,
    minima: float | None = 0.10,
    situacao: str = "6",
) -> str:
    """Uma linha em bling_orders + uma linha-item no snapshot verificar_margem.

    Default = o cenário do print do Eduardo: plataforma confiável (shopee) com
    líquido real NULL → Efetivo em branco, linha Pendente por "aguardando
    saldo da plataforma". marketplace_margem fica NULL de propósito (sem real
    não existe margem oficial — igual em produção).
    """
    item_id = str(uuid.uuid4())
    db.add(
        BlingOrder(
            bling_id=bling_id,
            numero=pedido,
            item_codigo=f"sku-{pedido}",
            item_index=0,
            situacao=situacao,
        )
    )
    await db.commit()
    await db.execute(
        text(
            """
            INSERT INTO verificar_margem (
                bling_order_item_id, pedido_bling, bling_id, sku,
                situacao, situacao_nome, plataforma_bling, loja_nome,
                item_proportion, marketplace_margem, margem_minima,
                bling_valorbase_item, bling_custo_produtos,
                marketplace_liquido_base_margem_item, data
            ) VALUES (
                :id, :pedido, :bling_id, :sku,
                :situacao, 'Em aberto', :plataforma, 'Loja Teste',
                1, NULL, :minima,
                :valorbase, :custo,
                :liquido, :data
            )
            """
        ),
        {
            "id": item_id,
            "pedido": pedido,
            "bling_id": bling_id,
            "sku": f"sku-{pedido}",
            "situacao": situacao,
            "plataforma": plataforma,
            "minima": minima,
            "valorbase": valorbase,
            "custo": custo,
            "liquido": liquido,
            "data": datetime.now(UTC),
        },
    )
    await db.commit()
    return item_id


async def _listar_linha(client, pedido: str) -> dict:
    res = await client.get(
        "/api/margens/marketplace", params={"search": pedido, "status": "all"}
    )
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) == 1, items
    return items[0]


async def _saldo_manual_no_banco(db: AsyncSession, item_id: str) -> float | None:
    row = (
        await db.execute(
            text("SELECT valor FROM margem_saldo_manual WHERE bling_order_item_id = :i"),
            {"i": item_id},
        )
    ).first()
    return None if row is None else float(row.valor)


async def test_aguardando_saldo_fica_pendente_e_em_branco(client, db, make_user, auth_as):
    """Baseline do print: confiável sem real → Efetivo NULL + Pendente."""
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    await _seed_item(db, pedido="900100", bling_id=900100)

    linha = await _listar_linha(client, "900100")
    assert linha["saldo_plataforma"] is None
    assert linha["saldo_manual"] is None
    assert linha["saldo_efetivo"] is None
    assert linha["saldo_final"] is None
    assert linha["attention_saldo"] is True
    assert linha["status"] == "Pendente"


async def test_saldo_manual_preenche_e_aprova_quando_margem_ok(
    client, db, make_user, auth_as
):
    """Digitar 150 (custo 100, mínima 10%) → margem 50% → aprova sozinho e a
    linha sai do "aguardando saldo"; o valor aparece no Efetivo/Final."""
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    item_id = await _seed_item(db, pedido="900101", bling_id=900101)

    res = await client.put(
        f"/api/margens/marketplace/{item_id}/saldo-manual", json={"valor": 150.0}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "Aprovado"
    assert body["valor"] == 150.0
    assert body["margem"] == pytest.approx(0.5)

    assert await _saldo_manual_no_banco(db, item_id) == 150.0
    linha = await _listar_linha(client, "900101")
    assert linha["saldo_manual"] == 150.0
    assert linha["saldo_efetivo"] == 150.0
    assert linha["saldo_final"] == 150.0
    assert linha["saldo_plataforma"] is None  # coluna Plataforma continua honesta
    assert linha["attention_saldo"] is False
    assert linha["status"] == "Aprovado"
    assert linha["margem_pos_reembolso"] == pytest.approx(0.5)


async def test_saldo_manual_baixo_segura_como_pendente(client, db, make_user, auth_as):
    """Digitar 90 (custo 100) → margem -10% < mínima → NÃO aprova: fica
    Pendente pra decisão humana (mesma régua da edição das outras plataformas)."""
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    item_id = await _seed_item(db, pedido="900102", bling_id=900102)

    res = await client.put(
        f"/api/margens/marketplace/{item_id}/saldo-manual", json={"valor": 90.0}
    )
    assert res.status_code == 200
    assert res.json()["status"] == "Pendente"
    assert res.json()["margem"] == pytest.approx(-0.1)

    linha = await _listar_linha(client, "900102")
    assert linha["saldo_efetivo"] == 90.0
    assert linha["status"] == "Pendente"


async def test_apagar_saldo_manual_volta_ao_automatico(client, db, make_user, auth_as):
    """valor=None apaga o digitado: Efetivo volta a "—", o status gravado é
    limpo e o gatilho "aguardando saldo" volta a segurar a linha."""
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    item_id = await _seed_item(db, pedido="900103", bling_id=900103)

    ok = await client.put(
        f"/api/margens/marketplace/{item_id}/saldo-manual", json={"valor": 150.0}
    )
    assert ok.json()["status"] == "Aprovado"

    res = await client.put(
        f"/api/margens/marketplace/{item_id}/saldo-manual", json={"valor": None}
    )
    assert res.status_code == 200
    assert res.json() == {"ok": True, "valor": None, "status": None}

    assert await _saldo_manual_no_banco(db, item_id) is None
    linha = await _listar_linha(client, "900103")
    assert linha["saldo_manual"] is None
    assert linha["saldo_efetivo"] is None
    assert linha["attention_saldo"] is True
    assert linha["status"] == "Pendente"


async def test_repasse_real_vence_o_manual(client, db, make_user, auth_as):
    """Quando o líquido REAL sincroniza, ele manda (regra de 01/09): o
    Efetivo mostra o real, e o manual fica só de registro no payload."""
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    item_id = await _seed_item(db, pedido="900104", bling_id=900104)

    await client.put(
        f"/api/margens/marketplace/{item_id}/saldo-manual", json={"valor": 150.0}
    )
    # settlement chegou depois, com outro valor
    await db.execute(
        text(
            "UPDATE verificar_margem "
            "SET marketplace_liquido_base_margem_item = 120 "
            "WHERE bling_order_item_id = :i"
        ),
        {"i": item_id},
    )
    await db.commit()

    linha = await _listar_linha(client, "900104")
    assert linha["saldo_plataforma"] == 120.0
    assert linha["saldo_efetivo"] == 120.0  # real vence
    assert linha["saldo_manual"] == 150.0  # registro do que foi digitado


async def test_saldo_manual_recusado_fora_do_caso_certo(client, db, make_user, auth_as):
    """Guard-rails: plataforma não-ancorada (Amazon) usa o lápis normal
    (grava no Bling); e com o real JÁ sincronizado o manual seria ignorado."""
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    amazon = await _seed_item(db, pedido="900105", bling_id=900105, plataforma="amazon")
    com_real = await _seed_item(
        db, pedido="900106", bling_id=900106, liquido=120.0
    )

    res = await client.put(
        f"/api/margens/marketplace/{amazon}/saldo-manual", json={"valor": 150.0}
    )
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "saldo_manual_nao_aplicavel"

    res = await client.put(
        f"/api/margens/marketplace/{com_real}/saldo-manual", json={"valor": 150.0}
    )
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "saldo_plataforma_ja_sincronizado"


async def test_saldo_manual_exige_permissao_de_edicao(client, db, make_user, auth_as):
    user = await make_user(permissions={"margem": {"view": True}})
    auth_as(user)
    item_id = await _seed_item(db, pedido="900107", bling_id=900107)

    res = await client.put(
        f"/api/margens/marketplace/{item_id}/saldo-manual", json={"valor": 150.0}
    )
    assert res.status_code == 403
