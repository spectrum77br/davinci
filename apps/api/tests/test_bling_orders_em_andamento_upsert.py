"""upsert_order não pode nullar em_andamento_data via on_conflict_do_update.

Fix `eaa79ae`+: 774 pedidos em prod (22-30/05, situações 83953/83957/545902)
tiveram em_andamento_data nullada porque o caminho full_replace do
upsert_order chamava on_conflict_do_update sobrescrevendo todas as colunas,
incluindo em_andamento_data, mesmo quando nova_data vinha None. Companion
do trigger DB `bling_orders_protect_data` — defesa em profundidade.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder
from app.services import bling_orders as bo
from app.services.bling_orders import upsert_order


@pytest_asyncio.fixture(autouse=True)
async def _ensure_unique_index(db: AsyncSession):
    """Replica a UNIQUE (bling_id, item_index) criada pela migration 0111
    — `Base.metadata.create_all` no setup de teste não recria índices
    de migrations. Sem esse índice, ON CONFLICT (bling_id, item_index)
    estoura InvalidColumnReferenceError."""
    await db.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_bling_orders_bling_id_item_index "
        "ON bling_orders (bling_id, item_index)"
    ))
    await db.commit()
    yield


def _raw(bling_id: int, situacao_id: int, itens: list[dict]) -> dict:
    """Payload Bling mínimo válido pro upsert."""
    return {
        "id": bling_id,
        "numero": f"PED-{bling_id}",
        "loja": {"id": 1},
        "situacao": {"id": situacao_id},
        "itens": itens,
    }


def _item(codigo: str, idx: int = 1) -> dict:
    return {
        "id": idx,
        "codigo": codigo,
        "quantidade": 1,
        "valor": "10.00",
        "produto": {"id": idx * 10},
    }


async def _seed_order(
    db: AsyncSession, *, bling_id: int, situacao: str,
    em_andamento_data: date | None,
) -> None:
    """Insere uma row direta no DB pra simular pedido pré-existente."""
    db.add(BlingOrder(
        id=uuid4(),
        bling_id=bling_id,
        item_index=0,
        numero=f"PED-{bling_id}",
        situacao=situacao,
        em_andamento_data=em_andamento_data,
        item_codigo="OLD-SKU",
        item_quantidade=1,
    ))
    await db.commit()


@pytest.mark.asyncio
async def test_full_replace_preserva_data_existente_quando_nova_e_none(
    db: AsyncSession, monkeypatch
):
    """Cenário central do bug: full_replace chama on_conflict_do_update,
    nova_data vem None, COALESCE deve preservar o valor antigo."""
    await _seed_order(
        db, bling_id=7001, situacao="83953",
        em_andamento_data=date(2026, 5, 30),
    )

    # Força nova_data=None mesmo havendo data_existente. Bug-style:
    # simula o estado onde _next_em_andamento_data devolveria None
    # (ex.: sync após o próprio nullify se autopropagar).
    monkeypatch.setattr(
        bo, "_next_em_andamento_data", lambda **_kw: None,
    )

    # Situacao=6 força full_replace.
    raw = _raw(7001, 6, [_item("NEW-SKU", 1)])
    await upsert_order(db, raw)
    await db.commit()

    rows = (await db.execute(
        select(BlingOrder).where(BlingOrder.bling_id == 7001)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].em_andamento_data == date(2026, 5, 30), (
        "COALESCE no on_conflict_do_update deve preservar o valor antigo "
        "quando nova_data vem None"
    )


@pytest.mark.asyncio
async def test_full_replace_15_para_6_preserva_via_next_data(db: AsyncSession):
    """Transição 15→6 com data existente: _next_em_andamento_data já
    retorna data_existente (branch 2). Camada acima do COALESCE — testa
    que o caminho 'natural' continua preservando."""
    await _seed_order(
        db, bling_id=7002, situacao="15",
        em_andamento_data=date(2026, 5, 25),
    )

    raw = _raw(7002, 6, [_item("NEW-SKU", 1)])
    await upsert_order(db, raw)
    await db.commit()

    rows = (await db.execute(
        select(BlingOrder).where(BlingOrder.bling_id == 7002)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].em_andamento_data == date(2026, 5, 25)


@pytest.mark.asyncio
async def test_primeira_gravacao_83965_carimba_hoje(db: AsyncSession):
    """Pedido novo (sem prev_rows): 83965 vai pro full_replace e carimba
    a data operacional de hoje."""
    raw = _raw(7003, 83965, [_item("FIRST-SKU", 1)])
    await upsert_order(db, raw)
    await db.commit()

    rows = (await db.execute(
        select(BlingOrder).where(BlingOrder.bling_id == 7003)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].em_andamento_data is not None
    # data operacional = data UTC ajustada (~hoje, tolerância 1 dia pra
    # rodar à meia-noite).
    today = datetime.now(UTC).date()
    assert abs((rows[0].em_andamento_data - today).days) <= 1


@pytest.mark.asyncio
async def test_full_replace_transicao_83965_para_15_sobrescreve_com_hoje(
    db: AsyncSession,
):
    """83965→15 é o ÚNICO caso de overwrite legítimo: agência confirmou
    AGORA, então a data vira o dia da confirmação (hoje), sobrescrevendo
    o provisório do 83965. Não pode regredir."""
    await _seed_order(
        db, bling_id=7004, situacao="83965",
        em_andamento_data=date(2026, 5, 30),
    )

    # Itens diferentes força full_replace mesmo com situacao != "6".
    raw = _raw(7004, 15, [_item("DIFFERENT-SKU", 2)])
    await upsert_order(db, raw)
    await db.commit()

    rows = (await db.execute(
        select(BlingOrder).where(BlingOrder.bling_id == 7004)
    )).scalars().all()
    assert len(rows) == 1
    today = datetime.now(UTC).date()
    assert rows[0].em_andamento_data is not None
    assert abs((rows[0].em_andamento_data - today).days) <= 1, (
        "83965→15 deve sobrescrever pra hoje (dia da confirmação), "
        "NÃO preservar a data antiga de 30/05"
    )
