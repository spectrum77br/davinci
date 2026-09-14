"""Catch-up do snapshot diário de Estoque Bling (valuation_estoque_snapshot).

O cron das 08:00 BRT some quando o worker é reiniciado por deploy na janela
(02/09, 11/09 e 14/09 ficaram sem linha). `repor_snapshot_se_faltar` roda
toda hora + no startup e só refaz o crawl quando já passou das 08:00 (SP) e o
dia ainda não tem linha em valuation_estoque_bling_diario.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import valuation_estoque_snapshot as svc

_SP = ZoneInfo("America/Sao_Paulo")


def _espiona_crawl(monkeypatch) -> list[int]:
    chamadas: list[int] = []

    async def fake(session):
        chamadas.append(1)
        return {"status": "ok", "data": "2026-09-14"}

    monkeypatch.setattr(svc, "run_valuation_estoque_snapshot", fake)
    return chamadas


@pytest.mark.asyncio
async def test_antes_das_8_nao_roda(db: AsyncSession, monkeypatch):
    chamadas = _espiona_crawl(monkeypatch)
    r = await svc.repor_snapshot_se_faltar(
        db, agora=datetime(2026, 9, 14, 7, 59, tzinfo=_SP)
    )
    assert r["status"] == "cedo"
    assert chamadas == []


@pytest.mark.asyncio
async def test_dia_sem_linha_refaz_o_crawl(db: AsyncSession, monkeypatch):
    chamadas = _espiona_crawl(monkeypatch)
    r = await svc.repor_snapshot_se_faltar(
        db, agora=datetime(2026, 9, 14, 8, 20, tzinfo=_SP)
    )
    assert r["status"] == "ok"
    assert chamadas == [1]


@pytest.mark.asyncio
async def test_dia_com_linha_nao_roda(db: AsyncSession, monkeypatch):
    chamadas = _espiona_crawl(monkeypatch)
    await db.execute(text(
        "INSERT INTO valuation_estoque_bling_diario (data, total_qtd, total_valor, por_local) "
        "VALUES (:d, 1, 1, '{}'::jsonb)"
    ).bindparams(d=datetime(2026, 9, 14).date()))
    await db.commit()
    r = await svc.repor_snapshot_se_faltar(
        db, agora=datetime(2026, 9, 14, 8, 20, tzinfo=_SP)
    )
    assert r["status"] == "ja_tem"
    assert chamadas == []


@pytest.mark.asyncio
async def test_hora_e_avaliada_em_sao_paulo(db: AsyncSession, monkeypatch):
    # 10:30 UTC = 07:30 BRT → ainda cedo, mesmo que em UTC já passe das 8.
    chamadas = _espiona_crawl(monkeypatch)
    r = await svc.repor_snapshot_se_faltar(
        db, agora=datetime(2026, 9, 14, 10, 30, tzinfo=ZoneInfo("UTC"))
    )
    assert r["status"] == "cedo"
    assert chamadas == []
