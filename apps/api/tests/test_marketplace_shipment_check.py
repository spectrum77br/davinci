"""COALESCE no UPDATE de `marketplace_shipment_check`.

Cobre o fix do segundo caminho de drift de data: o serviço sempre promove
`situacao` pra 15 (Atendido), mas só carimba `em_andamento_data` quando
ainda está NULL — nunca sobrescreve uma data já correta (ex.: Shopee/ML
reportando D+1/D+2 por delay de processamento, que antes re-carimbava).

Testa o SQL exato do bloco "Local stamp" (linhas ~258-268).
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder

_SHIPPED_SITUACAO = "15"


async def _make_order(
    db: AsyncSession, *, bling_id: int, situacao: str,
    em_andamento_data: date | None,
) -> BlingOrder:
    o = BlingOrder(
        bling_id=bling_id,
        numero=str(bling_id),
        item_codigo="sku-test",
        item_index=0,
        situacao=situacao,
        em_andamento_data=em_andamento_data,
    )
    db.add(o)
    await db.commit()
    await db.refresh(o)
    return o


def _stamp_update(bling_id: int, ship_date: date):
    """SQL idêntico ao que o serviço roda (bloco Local stamp)."""
    return (
        update(BlingOrder)
        .where(BlingOrder.bling_id == bling_id)
        .values(
            em_andamento_data=func.coalesce(
                BlingOrder.em_andamento_data, ship_date,
            ),
            situacao=_SHIPPED_SITUACAO,
        )
    )


@pytest.mark.asyncio
async def test_preserva_em_andamento_data_ja_preenchida(db: AsyncSession):
    """Marketplace reporta D+1 mas o pedido já tem ship-date correto:
    preserva data, mas atualiza situacao."""
    o = await _make_order(
        db, bling_id=900001, situacao="83965",
        em_andamento_data=date(2026, 5, 30),
    )
    # Marketplace reporta um dia depois (D+1)
    await db.execute(_stamp_update(900001, date(2026, 6, 1)))
    await db.commit()
    await db.refresh(o)
    assert o.em_andamento_data == date(2026, 5, 30)  # preservada
    assert o.situacao == "15"  # promovida


@pytest.mark.asyncio
async def test_carimba_quando_em_andamento_data_nula(db: AsyncSession):
    """Pedido sem ship-date (situacao=6 ou 83965 sem data): carimba com
    a data real do marketplace E promove situacao."""
    o = await _make_order(
        db, bling_id=900002, situacao="6",
        em_andamento_data=None,
    )
    await db.execute(_stamp_update(900002, date(2026, 5, 30)))
    await db.commit()
    await db.refresh(o)
    assert o.em_andamento_data == date(2026, 5, 30)  # carimbada
    assert o.situacao == "15"


@pytest.mark.asyncio
async def test_carimba_com_fallback_quando_marketplace_sem_data(db: AsyncSession):
    """Quando marketplace não devolve real_ship_date, o caller passa
    _operational_ship_date(now()) como ship_date. COALESCE preserva
    em_andamento existente do mesmo jeito."""
    fallback = date(2026, 6, 1)
    # cenário (a): data já preenchida — fallback é descartado
    o_a = await _make_order(
        db, bling_id=900003, situacao="83965",
        em_andamento_data=date(2026, 5, 28),
    )
    await db.execute(_stamp_update(900003, fallback))
    await db.commit()
    await db.refresh(o_a)
    assert o_a.em_andamento_data == date(2026, 5, 28)
    assert o_a.situacao == "15"

    # cenário (b): data NULL — fallback aplica
    o_b = await _make_order(
        db, bling_id=900004, situacao="83965",
        em_andamento_data=None,
    )
    await db.execute(_stamp_update(900004, fallback))
    await db.commit()
    await db.refresh(o_b)
    assert o_b.em_andamento_data == fallback
    assert o_b.situacao == "15"


@pytest.mark.asyncio
async def test_situacao_promovida_mesmo_sem_mudanca_de_data(db: AsyncSession):
    """Garantia explícita: o serviço NUNCA deixa de promover situacao=15
    por causa do COALESCE. Filtrar `em_andamento.is_(None)` no WHERE
    teria quebrado esse caso — por isso usa COALESCE."""
    o = await _make_order(
        db, bling_id=900005, situacao="83965",
        em_andamento_data=date(2026, 5, 25),
    )
    await db.execute(_stamp_update(900005, date(2026, 6, 1)))
    await db.commit()
    await db.refresh(o)
    assert o.situacao == "15"
    assert o.em_andamento_data == date(2026, 5, 25)
