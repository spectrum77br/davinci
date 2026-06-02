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


# ─── _load_candidates: critério de seleção ───────────────────────────


async def _make_candidate(
    db: AsyncSession,
    *,
    bling_id: int,
    situacao: str,
    em_andamento_data: date | None,
    numeroloja: str = "MP-1",
    loja: str = "100",
) -> BlingOrder:
    """Candidato precisa de numeroloja + loja preenchidos pra entrar
    no filtro do _load_candidates."""
    o = BlingOrder(
        bling_id=bling_id,
        numero=str(bling_id),
        numeroloja=numeroloja,
        loja=loja,
        item_codigo="sku-test",
        item_index=0,
        situacao=situacao,
        em_andamento_data=em_andamento_data,
    )
    db.add(o)
    await db.commit()
    await db.refresh(o)
    return o


@pytest.mark.asyncio
async def test_load_candidates_inclui_83965_com_data(db: AsyncSession):
    """Regressão guard: pedido em 83965 COM em_andamento_data deve
    entrar como candidato. Antes o filtro `IS NULL` o bloqueava,
    e o fix e081e0d carimba data já em 83965 — então TODOS os
    novos pedidos em 83965 tinham data e nenhum entrava."""
    from app.services.marketplace_shipment_check import _load_candidates

    await _make_candidate(
        db, bling_id=901001, situacao="83965",
        em_andamento_data=date(2026, 6, 2),
    )
    rows = await _load_candidates(db)
    assert any(r.bling_id == 901001 for r in rows)


@pytest.mark.asyncio
async def test_load_candidates_inclui_83965_sem_data(db: AsyncSession):
    """Pedidos antigos (pré-e081e0d) sem data continuam entrando."""
    from app.services.marketplace_shipment_check import _load_candidates

    await _make_candidate(
        db, bling_id=901002, situacao="83965", em_andamento_data=None,
    )
    rows = await _load_candidates(db)
    assert any(r.bling_id == 901002 for r in rows)


@pytest.mark.asyncio
async def test_load_candidates_inclui_6_em_aberto(db: AsyncSession):
    """Situação 6 (Em aberto) é destinada a virar 83965 + 15 — entra."""
    from app.services.marketplace_shipment_check import _load_candidates

    await _make_candidate(
        db, bling_id=901003, situacao="6", em_andamento_data=None,
    )
    rows = await _load_candidates(db)
    assert any(r.bling_id == 901003 for r in rows)


@pytest.mark.asyncio
async def test_load_candidates_exclui_situacao_15(db: AsyncSession):
    """Pedido já promovido a 15 NÃO precisa de nova checagem."""
    from app.services.marketplace_shipment_check import _load_candidates

    await _make_candidate(
        db, bling_id=901004, situacao="15",
        em_andamento_data=date(2026, 6, 2),
    )
    rows = await _load_candidates(db)
    assert all(r.bling_id != 901004 for r in rows)


@pytest.mark.asyncio
async def test_load_candidates_exclui_83953_entregue(db: AsyncSession):
    """83953 (Entregue) já passou pelo fluxo — excluído."""
    from app.services.marketplace_shipment_check import _load_candidates

    await _make_candidate(
        db, bling_id=901005, situacao="83953",
        em_andamento_data=date(2026, 6, 2),
    )
    rows = await _load_candidates(db)
    assert all(r.bling_id != 901005 for r in rows)


@pytest.mark.asyncio
async def test_load_candidates_exclui_fora_da_janela(db: AsyncSession):
    """`created_at` muito antigo (fora de _CANDIDATE_WINDOW) é excluído.
    Cria sem `created_at` (server_default=now()), depois UPDATE manual
    pra empurrar pra 1 ano atrás — fora de qualquer janela razoável."""
    from datetime import UTC, datetime, timedelta

    from app.services.marketplace_shipment_check import _load_candidates

    o = await _make_candidate(
        db, bling_id=901006, situacao="83965", em_andamento_data=None,
    )
    old_ts = datetime.now(UTC) - timedelta(days=365)
    await db.execute(
        update(BlingOrder).where(BlingOrder.bling_id == 901006).values(created_at=old_ts)
    )
    await db.commit()
    await db.refresh(o)
    rows = await _load_candidates(db)
    assert all(r.bling_id != 901006 for r in rows)
