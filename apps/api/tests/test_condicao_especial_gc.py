"""Condição Especial — limpeza automática das encerradas (services/condicao_especial).

Pedido de 10/09/2026: condição com período que já passou não fica parada no
painel. Apaga só depois da carência de 30 dias (pedido feito dentro do
período pode ainda estar em triagem); condição sem período nunca expira.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Segment, SegmentSpecialDate
from app.services.condicao_especial import CARENCIA_DIAS, limpar_encerradas

pytestmark = pytest.mark.asyncio


async def test_apaga_so_periodo_encerrado_ha_mais_de_30_dias(db: AsyncSession) -> None:
    seg = Segment(name="Seg GC", slug=f"seg-gc-{uuid.uuid4().hex[:6]}")
    db.add(seg)
    await db.flush()
    hoje = date(2026, 9, 10)
    velha = SegmentSpecialDate(
        segment_id=seg.id,
        date_start=hoje - timedelta(days=CARENCIA_DIAS + 10),
        date_end=hoje - timedelta(days=CARENCIA_DIAS + 1),
    )
    na_carencia = SegmentSpecialDate(
        segment_id=seg.id,
        date_start=hoje - timedelta(days=CARENCIA_DIAS + 5),
        date_end=hoje - timedelta(days=CARENCIA_DIAS),
    )
    encerrada_ontem = SegmentSpecialDate(
        segment_id=seg.id, date_start=hoje - timedelta(days=7), date_end=hoje - timedelta(days=1)
    )
    sem_periodo = SegmentSpecialDate(segment_id=seg.id, nome_contem="M3", min_margin=None)
    db.add_all([velha, na_carencia, encerrada_ontem, sem_periodo])
    await db.commit()

    apagadas = await limpar_encerradas(db, hoje_sp=hoje)
    await db.commit()
    assert apagadas == 1

    restantes = set(
        (
            await db.execute(
                select(SegmentSpecialDate.id).where(SegmentSpecialDate.segment_id == seg.id)
            )
        ).scalars()
    )
    assert velha.id not in restantes
    assert {na_carencia.id, encerrada_ontem.id, sem_periodo.id} <= restantes
