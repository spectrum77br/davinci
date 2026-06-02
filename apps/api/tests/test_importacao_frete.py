"""Aba Frete do Celular (etapa 4) — endpoint /frete agrega items dos
lotes + ajustes manuais; PATCH /lote_item toggle pago; POST /lote_ajuste
cria linha em ImportResumo com transportadora setada.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ImportCotacaoParams,
    ImportLote,
    ImportLoteItem,
    ImportProduct,
    ImportResumo,
    User,
)


@pytest.fixture
def user_imp_edit(make_user):
    async def _f():
        return await make_user(
            permissions={"importacao": {"view": True, "edit": True, "delete": True}},
        )

    return _f


async def _seed_frete_basics(db: AsyncSession) -> dict[str, str]:
    """Cria 1 produto celular + 1 lote (com transportadora) + 1 item
    (quantidade 10). Frete params com valores conhecidos pra checar
    saldo na agregação."""
    db.add(ImportCotacaoParams(
        categoria="celular",
        taxa_cambio=Decimal("5.10"),
        frete_regular_pct=Decimal("0.16"),
        frete_swap_pct=Decimal("0.06"),
        frete_acessorios_pct=Decimal("0.20"),
        adicional=Decimal("12.00"),
    ))
    prod = ImportProduct(
        id=uuid4(),
        categoria="celular",
        sku="i220.sa",
        modelo_bling="Apple iPhone 17 Pro - Azul",
        custo_bling=Decimal("0"),
        valor_brl_realizado=Decimal("332.00"),
        frete_type="regular",
    )
    db.add(prod)
    await db.flush()
    lote = ImportLote(
        id=uuid4(),
        categoria="celular",
        nome="AG242",
        abertura=date(2026, 4, 22),
        fechamento=None,
        transportadora="Cargo X",
    )
    db.add(lote)
    await db.flush()
    item = ImportLoteItem(
        id=uuid4(),
        lote_id=lote.id, product_id=prod.id, quantidade=10,
    )
    db.add(item)
    await db.commit()
    return {"prod_id": str(prod.id), "lote_id": str(lote.id), "item_id": str(item.id)}


@pytest.mark.asyncio
async def test_frete_list_agrega_item_aberto(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], user_imp_edit,
):
    """Lote sem fechamento: saldo=null (pendente), total entra em
    `total_a_entregar`."""
    auth_as(await user_imp_edit())
    await _seed_frete_basics(db)

    r = await client.get("/api/importacao/frete?categoria=celular&pago=false")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["rows"]) == 1
    row = body["rows"][0]
    assert row["kind"] == "item"
    assert row["transportadora"] == "Cargo X"
    assert row["lote_nome"] == "AG242"
    assert row["fechamento"] is None
    assert row["quantidade"] == 10
    assert Decimal(row["valor_unit"]) == Decimal("332.00")
    assert Decimal(row["total"]) == Decimal("3320.00")  # 10 × 332
    assert Decimal(row["frete_pct"]) == Decimal("0.16")
    assert row["saldo"] is None  # sem fechamento → pendente
    assert row["pago"] is False
    assert body["transportadoras"] == ["Cargo X"]
    assert Decimal(body["total_a_entregar"]) == Decimal("3320.00")
    assert Decimal(body["saldo_a_pagar"]) == Decimal("0.00")


@pytest.mark.asyncio
async def test_frete_list_lote_fechado_calcula_saldo(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], user_imp_edit,
):
    """Lote com fechamento: saldo = total × frete_pct (10×332×0.16 = 531.20).
    Como pago=false, entra em `saldo_a_pagar`."""
    auth_as(await user_imp_edit())
    ids = await _seed_frete_basics(db)
    # Fecha o lote.
    lote = await db.get(ImportLote, ids["lote_id"])
    lote.fechamento = date(2026, 4, 28)
    await db.commit()

    r = await client.get("/api/importacao/frete?categoria=celular&pago=false")
    body = r.json()
    row = body["rows"][0]
    assert row["fechamento"] == "2026-04-28"
    assert Decimal(row["saldo"]) == Decimal("531.20")
    assert Decimal(body["total_a_entregar"]) == Decimal("0.00")  # fechado, sai do "a entregar"
    assert Decimal(body["saldo_a_pagar"]) == Decimal("531.20")


@pytest.mark.asyncio
async def test_patch_lote_item_pago_remove_de_saldo_a_pagar(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], user_imp_edit,
):
    auth_as(await user_imp_edit())
    ids = await _seed_frete_basics(db)
    lote = await db.get(ImportLote, ids["lote_id"])
    lote.fechamento = date(2026, 4, 28)
    await db.commit()

    # Toggle pago.
    r = await client.patch(
        f"/api/importacao/lote_item/{ids['item_id']}",
        json={"pago": True},
    )
    assert r.status_code == 204

    # Agora saldo_a_pagar zera (com filtro pago=false retira a linha).
    r = await client.get("/api/importacao/frete?categoria=celular&pago=false")
    body = r.json()
    assert body["rows"] == []
    assert Decimal(body["saldo_a_pagar"]) == Decimal("0.00")

    # Sem filtro: linha volta mas saldo_a_pagar continua zero (pago=true exclui).
    r2 = await client.get("/api/importacao/frete?categoria=celular")
    body2 = r2.json()
    assert len(body2["rows"]) == 1
    assert body2["rows"][0]["pago"] is True
    assert Decimal(body2["saldo_a_pagar"]) == Decimal("0.00")


@pytest.mark.asyncio
async def test_post_lote_ajuste_aparece_em_frete_e_resumo(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], user_imp_edit,
):
    """Ajuste manual cria row em ImportResumo com transportadora set.
    Aparece na aba Frete (kind=ajuste) E na aba Resumo (lançamento)."""
    auth_as(await user_imp_edit())
    await _seed_frete_basics(db)

    r = await client.post("/api/importacao/lote_ajuste", json={
        "transportadora": "Outra Transp", "abertura": "2026-05-01",
        "saldo": "250.00", "obs": "frete extra", "categoria": "celular",
    })
    assert r.status_code == 201, r.text

    # Aba Frete: ajuste aparece com modelo=null, kind=ajuste, saldo
    # conta em saldo_a_pagar.
    rf = await client.get("/api/importacao/frete?categoria=celular")
    body = rf.json()
    ajustes = [x for x in body["rows"] if x["kind"] == "ajuste"]
    assert len(ajustes) == 1
    aj = ajustes[0]
    assert aj["transportadora"] == "Outra Transp"
    assert Decimal(aj["saldo"]) == Decimal("250.00")
    assert aj["modelo_bling"] is None
    assert aj["obs"] == "frete extra"
    assert Decimal(body["saldo_a_pagar"]) == Decimal("250.00")
    assert set(body["transportadoras"]) == {"Cargo X", "Outra Transp"}

    # Aba Resumo: mesma row aparece via /resumo.
    rr = await client.get("/api/importacao/resumo?categoria=celular")
    rbody = rr.json()
    assert len(rbody["items"]) == 1
    assert Decimal(rbody["items"][0]["saldo"]) == Decimal("250.00")
    assert rbody["items"][0]["transportadora"] == "Outra Transp"


@pytest.mark.asyncio
async def test_frete_filtro_transportadora(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], user_imp_edit,
):
    auth_as(await user_imp_edit())
    await _seed_frete_basics(db)
    # Cria ajuste manual com outra transportadora.
    db.add(ImportResumo(
        categoria="celular", data=date(2026, 5, 1),
        saldo=Decimal("100.00"), transportadora="Outra Transp",
    ))
    await db.commit()

    r = await client.get("/api/importacao/frete?categoria=celular&transportadora=Cargo%20X")
    body = r.json()
    assert all(x["transportadora"] == "Cargo X" for x in body["rows"])
