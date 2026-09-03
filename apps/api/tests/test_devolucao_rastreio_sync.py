# ruff: noqa: E501
"""Rastreio AUTOMÁTICO do pacote que volta (Eduardo, 03/09).

"o TikTok não está pegando o número de rastreio correto" / "precisa sempre
estar atualizadinho" — o job `devolucao_rastreio_sync.run` pergunta ao
marketplace pela devolução de cada pedido em Aguardando Devolução, grava em
`devolucao_rastreio.*_auto` e registra os códigos Correios no 17track.
Aqui os fetchers dos marketplaces são falsos (contrato ReturnInfo).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DevolucaoRastreio, Logistica
from app.services import devolucao_rastreio_sync as svc
from app.services import logistica_meli, logistica_shopee, logistica_tiktok, logistica_track
from app.services.devolucao_returns import ReturnInfo, epoch_to_dt, iso_to_dt

pytestmark = pytest.mark.asyncio


def _info(fonte: str, **kw) -> ReturnInfo:
    base = {
        "fonte": fonte, "status": "BUYER_SHIPPED_ITEM", "tracking": "AP418496864BR",
        "carrier": "Correios",
        "created_at": datetime(2026, 8, 24, 12, 0, tzinfo=UTC),
        "updated_at": datetime(2026, 9, 3, 10, 0, tzinfo=UTC), "return_id": "ret-1",
    }
    base.update(kw)
    return ReturnInfo(**base)


@pytest.fixture
def fakes(monkeypatch):
    """Fetchers falsos por marketplace + 17track falso; devolve os registros."""
    calls: dict[str, list] = {"tiktok": [], "shopee": [], "ml": [], "register": []}
    respostas: dict[str, dict[str, ReturnInfo]] = {"tiktok": {}, "shopee": {}, "ml": {}}

    def _mk(key):
        async def _fn(session, linhas):
            calls[key].append([row.pedido_bling for row in linhas])
            return respostas[key]
        return _fn

    monkeypatch.setattr(logistica_tiktok, "returns_por_pedido", _mk("tiktok"), raising=False)
    monkeypatch.setattr(logistica_shopee, "returns_por_pedido", _mk("shopee"), raising=False)
    monkeypatch.setattr(logistica_meli, "returns_por_pedido", _mk("ml"), raising=False)

    async def _register(numbers):
        calls["register"].append(list(numbers))
        return {"ok": True}

    monkeypatch.setattr(logistica_track, "register", _register)
    return calls, respostas


async def _seed_logistica(db: AsyncSession, pedido: str, plataforma: str, **kw) -> Logistica:
    row = Logistica(pedido_bling=pedido, plataforma=plataforma, pedido_marketplace=f"mk-{pedido}", conta="x", **kw)
    db.add(row)
    await db.commit()
    return row


async def test_grava_auto_e_registra_correios(db: AsyncSession, fakes):
    calls, respostas = fakes
    p_tt, p_sh, p_ml = (f"4{uuid4().hex[:6]}" for _ in range(3))
    await _seed_logistica(db, p_tt, "TikTok")
    await _seed_logistica(db, p_sh, "Shopee")
    await _seed_logistica(db, p_ml, "Mercado Livre")
    respostas["tiktok"][p_tt] = _info("tiktok")
    # Shopee: só reembolso, sem código
    respostas["shopee"][p_sh] = _info("shopee", status="ACCEPTED", tracking="", carrier=None, return_id="2609XYZ")
    # ML: código da rede própria (não é Correios) → não registra no 17track
    respostas["ml"][p_ml] = _info("ml", status="shipped", tracking="TK3FULLVX", carrier="mercadoenvios")

    out = await svc.run(db, pedidos=[p_tt, p_sh, p_ml])

    assert out["devolucoes"] == 3 and out["gravados"] == 3
    # cada fetcher recebeu SÓ as linhas da própria plataforma
    assert calls["tiktok"] == [[p_tt]] and calls["shopee"] == [[p_sh]] and calls["ml"] == [[p_ml]]
    assert calls["register"] == [["AP418496864BR"]]

    rows = {
        r.pedido_bling: r
        for r in (
            await db.execute(select(DevolucaoRastreio).where(DevolucaoRastreio.pedido_bling.in_([p_tt, p_sh, p_ml])))
        ).scalars().all()
    }
    tt = rows[p_tt]
    assert tt.rastreio_auto == "AP418496864BR"
    assert tt.transportadora_auto == "Correios"
    assert tt.devolucao_status_auto == "BUYER_SHIPPED_ITEM"
    assert tt.fonte_auto == "tiktok"
    assert tt.devolucao_id_auto == "ret-1"
    assert tt.devolucao_criada_em == datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    assert tt.auto_sync_at is not None
    assert tt.rastreio is None and tt.localizacao is None  # manual intocado
    assert rows[p_sh].rastreio_auto is None
    assert rows[p_sh].devolucao_status_auto == "ACCEPTED"
    assert rows[p_ml].rastreio_auto == "TK3FULLVX"


async def test_codigo_novo_zera_localizacao_antiga_e_manual_permanece(db: AsyncSession, fakes):
    calls, respostas = fakes
    p = f"5{uuid4().hex[:6]}"
    await _seed_logistica(db, p, "TikTok")
    # Já existia manual + um código automático antigo com localização.
    db.add(
        DevolucaoRastreio(
            pedido_bling=p, localizacao="Recebido no CD", localizacao_data=datetime.now(UTC),
            rastreio_auto="AA111111111BR", localizacao_auto="Indaiatuba/SP — em trânsito",
            localizacao_auto_data=datetime.now(UTC),
        )
    )
    await db.commit()
    respostas["tiktok"][p] = _info("tiktok", tracking="BB222222222BR")

    await svc.run(db, pedidos=[p])

    row = (await db.execute(select(DevolucaoRastreio).where(DevolucaoRastreio.pedido_bling == p))).scalar_one()
    assert row.rastreio_auto == "BB222222222BR"
    assert row.localizacao_auto is None and row.localizacao_auto_data is None
    assert row.localizacao == "Recebido no CD"  # manual segue
    assert calls["register"] == [["BB222222222BR"]]

    # Mesmo código de novo → nada muda, não re-registra.
    calls["register"].clear()
    await svc.run(db, pedidos=[p])
    assert calls["register"] == []


async def test_marketplace_fora_do_ar_nao_derruba_os_outros(db: AsyncSession, fakes, monkeypatch):
    calls, respostas = fakes
    p_tt, p_sh = f"6{uuid4().hex[:6]}", f"7{uuid4().hex[:6]}"
    await _seed_logistica(db, p_tt, "TikTok")
    await _seed_logistica(db, p_sh, "Shopee")

    async def _boom(session, linhas):
        raise RuntimeError("shopee 500")

    monkeypatch.setattr(logistica_shopee, "returns_por_pedido", _boom, raising=False)
    respostas["tiktok"][p_tt] = _info("tiktok")

    out = await svc.run(db, pedidos=[p_tt, p_sh])
    assert out["devolucoes"] == 1 and out["gravados"] == 1


async def test_sem_fetcher_no_modulo_nao_quebra(db: AsyncSession, fakes, monkeypatch):
    calls, respostas = fakes
    p = f"8{uuid4().hex[:6]}"
    await _seed_logistica(db, p, "Mercado Livre")
    monkeypatch.delattr(logistica_meli, "returns_por_pedido", raising=False)
    out = await svc.run(db, pedidos=[p])
    assert out["devolucoes"] == 0


def test_helpers_de_data():
    # 1787872539 = create_time real da devolução do 291869 (TikTok, 27/08 20:15 BRT).
    assert epoch_to_dt(1787872539) == datetime(2026, 8, 27, 23, 15, 39, tzinfo=UTC)
    assert epoch_to_dt(1787872539000) == datetime(2026, 8, 27, 23, 15, 39, tzinfo=UTC)
    assert epoch_to_dt(None) is None and epoch_to_dt("x") is None and epoch_to_dt(0) is None
    assert iso_to_dt("2026-09-03T10:42:47Z") == datetime(2026, 9, 3, 10, 42, 47, tzinfo=UTC)
    assert iso_to_dt("2026-09-03T07:42:47-03:00") == datetime(2026, 9, 3, 10, 42, 47, tzinfo=UTC)
    assert iso_to_dt("") is None and iso_to_dt(None) is None
