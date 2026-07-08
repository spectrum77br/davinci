"""Ressurreição de produto 'E' (services.refresh_bling_stock).

Bug: `_reconcile_excluidos` marca `situacao='E'` quando o bling_product_id
some do sweep do /produtos, mas era MÃO ÚNICA — quando o produto voltava a
aparecer no Bling (reativado), nada o trazia de volta pra 'A'. O candidato
a reconciliação exige `situacao in ('A', None)` e o backfill de situacao só
toca NULL, então o 'E' ficava congelado pra sempre e o produto sumia do
Controle de Estoque e da busca de Correção de Estoque (devoluções).

Fix: no início de `_reconcile_excluidos`, todo produto local 'E' cujo bpid
VOLTOU no sweep (bpid ∈ seen_bpids) é restaurado pra 'A' — o /produtos não
lista apagados/excluídos, então reaparecer = vivo de novo no catálogo.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BackgroundJob,
    BackgroundJobType,
    Product,
    User,
    UserRole,
    UserStatus,
)
from app.services.refresh_bling_stock import _reconcile_excluidos


@pytest_asyncio.fixture
async def user(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:rs-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"rs-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _job(db: AsyncSession, user: User) -> BackgroundJob:
    # SYNC_ALL (valor antigo do enum) — o test DB não roda migrations, então o
    # enum background_job_type não tem valores novos como refresh_bling_stock.
    # O `job` não é usado no caminho de ressurreição; o tipo é irrelevante.
    j = BackgroundJob(type=BackgroundJobType.SYNC_ALL, created_by=user.id)
    db.add(j)
    await db.commit()
    await db.refresh(j)
    return j


@pytest.mark.asyncio
async def test_resurrect_stale_excluido_when_bpid_reappears(
    db: AsyncSession, user: User
):
    """Produto 'E' cujo bpid voltou no sweep → vira 'A'. Um 'A' de controle
    (também presente no sweep) fica intacto e não vira candidato."""
    stale = Product(
        user_id=user.id, sku="b017.28", name="Mala Listrada 28 - Rosa",
        stock=0, min_stock=0, situacao="E", formato="S",
        bling_product_id=16377810202,
    )
    control = Product(
        user_id=user.id, sku="aa1.sa", name="ativo",
        stock=5, min_stock=0, situacao="A", formato="S",
        bling_product_id=222,
    )
    db.add_all([stale, control])
    await db.commit()
    for p in (stale, control):
        await db.refresh(p)

    product_by_bpid = {16377810202: stale, 222: control}
    seen_bpids = {16377810202, 222}  # ambos vivos no Bling → sem candidatos
    summary: dict = {
        "reconcile_candidates": 0, "reconciled_excluido": 0,
        "reconciled_healed": 0, "reconciled_resurrected": 0,
    }
    job = await _job(db, user)

    # clients não-vazio pra passar o guard; nunca é chamado (candidatos = 0).
    await _reconcile_excluidos(
        db, [object()], product_by_bpid, seen_bpids, job, summary,
    )

    await db.refresh(stale)
    await db.refresh(control)
    assert stale.situacao == "A"
    assert control.situacao == "A"
    assert summary["reconciled_resurrected"] == 1
    assert "b017.28" in summary["resurrected_skus"]


@pytest.mark.asyncio
async def test_no_resurrect_when_bpid_still_missing(
    db: AsyncSession, user: User
):
    """Produto 'E' cujo bpid NÃO voltou no sweep permanece 'E' — não é
    ressuscitado (ele segue apagado/excluído no Bling)."""
    stale = Product(
        user_id=user.id, sku="gone.99", name="apagado",
        stock=0, min_stock=0, situacao="E", formato="S",
        bling_product_id=999,
    )
    alive = Product(
        user_id=user.id, sku="aa1.sa", name="ativo",
        stock=5, min_stock=0, situacao="A", formato="S",
        bling_product_id=222,
    )
    db.add_all([stale, alive])
    await db.commit()
    for p in (stale, alive):
        await db.refresh(p)

    product_by_bpid = {999: stale, 222: alive}
    seen_bpids = {222}  # 999 sumiu do sweep
    summary: dict = {
        "reconcile_candidates": 0, "reconciled_excluido": 0,
        "reconciled_healed": 0, "reconciled_resurrected": 0,
    }
    job = await _job(db, user)

    # 999 vira candidato (situacao='E' NÃO é 'A'/None → fora dos candidatos),
    # então o loop de verificação 1-a-1 não roda pra ele. Como o único
    # cliente é um stub que não implementa get_product, garantimos que não há
    # candidatos passando seen_bpids sem 999 mas com situacao 'E' (não entra
    # no filtro `p.situacao in ('A', None)`). Resultado: nada muda.
    await _reconcile_excluidos(
        db, [object()], product_by_bpid, seen_bpids, job, summary,
    )

    await db.refresh(stale)
    assert stale.situacao == "E"
    assert summary["reconciled_resurrected"] == 0
