"""Página Valuation — saldos do mês (Caixa / A Receber / Estoque).

Cada saldo é o ÚLTIMO VALOR PREENCHIDO do mês, campo a campo — não a última
linha. Quatro robôs gravam a linha do dia em horários diferentes, então a
linha de hoje fica parcial entre uma gravação e outra; pegar a última linha
mostrava "—" em Estoque/A Receber com o valor de ontem no banco (14/09 08:02).
Cobre:
  * campo vazio na última linha cai pro último dia em que foi preenchido;
  * zero conta como vazio (placeholder que a VPS grava ao criar a linha);
  * `*_em` traz a data da leitura de cada campo; `data_snapshot` ignora linha
    sem nenhum saldo (a da rentabilidade de ontem);
  * mês sem linha nenhuma → tudo None.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserRole, UserStatus
from app.routers.financeiro import _make_valuation_token

_SP = ZoneInfo("America/Sao_Paulo")


async def _admin(db: AsyncSession) -> User:
    email = f"val-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(
        open_id=f"email:{email}", email=email,
        role=UserRole.ADMIN, status=UserStatus.ACTIVE,
        permissions={}, sales_teams=None,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _headers() -> dict[str, str]:
    token, _ = _make_valuation_token()
    return {"X-Valuation-Token": token}


async def _linha(db: AsyncSession, dia: date, *, caixa, estoque, receber) -> None:
    await db.execute(text(
        "INSERT INTO valuation (data, caixa, estoque, receber) "
        "VALUES (:d, :c, :e, :r)"
    ).bindparams(d=dia, c=caixa, e=estoque, r=receber))
    await db.commit()


def _mes_anterior() -> date:
    """Mês-1 (SP): inteiro dentro da janela de 3 meses e sem depender de
    que dia é hoje (no dia 1 'ontem' cairia no mês anterior)."""
    hoje = datetime.now(_SP).date()
    return (hoje.replace(day=1) - timedelta(days=1)).replace(day=1)


def _mes(body: dict, mes: date) -> dict:
    for m in body["valuation_meses"]:
        if m["mes"] == mes.isoformat():
            return m
    raise AssertionError(f"mês {mes} ausente de valuation_meses")


@pytest.mark.asyncio
async def test_saldo_usa_ultimo_valor_preenchido_por_campo(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    mes = _mes_anterior()
    d1, d2, d3 = mes, mes + timedelta(days=9), mes + timedelta(days=10)
    # d1: linha completa do dia (caixa 0 = placeholder da VPS).
    await _linha(db, d1, caixa=0, estoque=100.0, receber=50.0)
    # d2: só o robô do caixa gravou; estoque veio 0 (placeholder), receber vazio.
    await _linha(db, d2, caixa=7.0, estoque=0, receber=None)
    # d3: linha da rentabilidade de ontem — nenhum saldo.
    await _linha(db, d3, caixa=None, estoque=None, receber=None)
    auth_as(await _admin(db))

    r = await client.get("/api/financeiro/valuation", headers=_headers())
    assert r.status_code == 200, r.text
    m = _mes(r.json(), mes)

    assert m["caixa"] == 7.0 and m["caixa_em"] == d2.isoformat()
    # Estoque 0 de d2 é vazio → volta pro d1. Receber vazio em d2 → d1.
    assert m["estoque"] == 100.0 and m["estoque_em"] == d1.isoformat()
    assert m["receber"] == 50.0 and m["receber_em"] == d1.isoformat()
    assert m["total"] == 157.0
    # Última data COM saldo é d2 (d3 não tem nenhum).
    assert m["data_snapshot"] == d2.isoformat()


@pytest.mark.asyncio
async def test_mes_sem_linha_fica_vazio(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    auth_as(await _admin(db))
    r = await client.get("/api/financeiro/valuation", headers=_headers())
    assert r.status_code == 200, r.text
    m = _mes(r.json(), _mes_anterior())
    assert m["caixa"] is None and m["estoque"] is None and m["receber"] is None
    assert m["total"] is None and m["data_snapshot"] is None
    assert m["caixa_em"] is None and m["estoque_em"] is None and m["receber_em"] is None
