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
from app.services import (
    logistica_meli,
    logistica_shopee,
    logistica_tiktok,
    logistica_track,
    logistica_track_sync,
)
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
    """Fetchers falsos por marketplace + 17track falso; devolve os registros.

    `respostas["17track"]` controla o falso: `desconhecidos` (o que o pull diz
    não conhecer), `ok` (None = aceita tudo que for registrado), `sem_quota`,
    `quarentena` (números presos). `calls` guarda register / sem_quota /
    quarentena pra os testes conferirem."""
    calls: dict[str, list] = {
        "tiktok": [], "shopee": [], "ml": [], "register": [], "sem_quota": [], "quarentena": [],
    }
    respostas: dict[str, dict] = {
        "tiktok": {}, "shopee": {}, "ml": {},
        "17track": {"desconhecidos": [], "ok": None, "sem_quota": False, "quarentena": set()},
    }

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
        t = respostas["17track"]
        ok = list(numbers) if t["ok"] is None else list(t["ok"])
        return {"ok": [] if t["sem_quota"] else ok, "sem_quota": t["sem_quota"]}

    async def _fetch_detalhado(numbers):
        desc = [n for n in numbers if n in respostas["17track"]["desconhecidos"]]
        return {"info": {}, "desconhecidos": desc}

    async def _em_quarentena(numeros):
        return {n for n in numeros if n in respostas["17track"]["quarentena"]}

    async def _por_de_quarentena(numeros):
        calls["quarentena"].append(list(numeros))

    async def _marcar_sem_quota(flag):
        calls["sem_quota"].append(flag)

    monkeypatch.setattr(logistica_track, "register", _register)
    monkeypatch.setattr(logistica_track, "fetch_detalhado", _fetch_detalhado)
    monkeypatch.setattr(logistica_track_sync, "em_quarentena", _em_quarentena)
    monkeypatch.setattr(logistica_track_sync, "por_de_quarentena", _por_de_quarentena)
    monkeypatch.setattr(logistica_track_sync, "marcar_sem_quota", _marcar_sem_quota)
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


async def test_reregistra_codigo_que_o_17track_nao_conhece(db: AsyncSession, fakes):
    """Vinicius 17/09 — 291955 (AP436496123BR): o código entrou em 04/09, dia em
    que a conta do 17track estava sem saldo; o registro falhou uma vez e nunca
    foi refeito, e a Localização ficou parada no status do TikTok. Agora o pull
    de cada rodada pergunta ao 17track, e o que ele não conhece é registrado
    de novo — mesmo não sendo código novo."""
    calls, respostas = fakes
    p = f"9{uuid4().hex[:6]}"
    await _seed_logistica(db, p, "TikTok")
    db.add(DevolucaoRastreio(pedido_bling=p, rastreio_auto="AP436496123BR"))
    await db.commit()
    respostas["tiktok"][p] = _info("tiktok", tracking="AP436496123BR")  # mesmo código
    respostas["17track"]["desconhecidos"] = ["AP436496123BR"]

    out = await svc.run(db, pedidos=[p])

    assert calls["register"] == [["AP436496123BR"]]
    assert out["codigos_17track"] == 1 and out["codigos_17track_pendentes"] == 0
    assert out["sem_quota"] is False
    assert calls["sem_quota"] == [False]  # aviso de saldo apagado: a conta respondeu
    assert calls["quarentena"] == []

    # 17track passou a conhecer → rodada seguinte não registra de novo.
    respostas["17track"]["desconhecidos"] = []
    calls["register"].clear()
    await svc.run(db, pedidos=[p])
    assert calls["register"] == []


async def test_sem_saldo_so_conta_o_que_o_17track_confirmou_e_tenta_de_novo(db: AsyncSession, fakes):
    """Antes: `register` era chamado uma vez, sem olhar a resposta, e "sem
    saldo" naquele minuto virava pacote sem localização pra sempre."""
    calls, respostas = fakes
    p = f"9{uuid4().hex[:6]}"
    await _seed_logistica(db, p, "TikTok")
    respostas["tiktok"][p] = _info("tiktok", tracking="AP442031490BR")
    respostas["17track"]["sem_quota"] = True

    out = await svc.run(db, pedidos=[p])

    assert calls["register"] == [["AP442031490BR"]]
    assert out["codigos_17track"] == 0 and out["codigos_17track_pendentes"] == 1
    assert out["sem_quota"] is True
    assert calls["sem_quota"] == [True]  # a tela da Logística mostra o aviso
    assert calls["quarentena"] == []  # saldo não é culpa do número

    # Saldo recarregado: o pull diz que o 17track não conhece → registra de novo.
    respostas["17track"]["sem_quota"] = False
    respostas["17track"]["desconhecidos"] = ["AP442031490BR"]
    calls["register"].clear()
    out = await svc.run(db, pedidos=[p])
    assert calls["register"] == [["AP442031490BR"]]
    assert out["codigos_17track"] == 1


async def test_recusa_que_nao_e_saldo_vai_pra_quarentena(db: AsyncSession, fakes):
    calls, respostas = fakes
    p = f"9{uuid4().hex[:6]}"
    await _seed_logistica(db, p, "TikTok")
    respostas["tiktok"][p] = _info("tiktok", tracking="XX000000000BR")
    respostas["17track"]["ok"] = []  # 17track recusou (formato/transportadora)

    out = await svc.run(db, pedidos=[p])

    assert calls["register"] == [["XX000000000BR"]]
    assert calls["quarentena"] == [["XX000000000BR"]]
    assert out["codigos_17track"] == 0

    # Preso na quarentena: a rodada seguinte não manda de novo.
    respostas["17track"]["quarentena"] = {"XX000000000BR"}
    respostas["17track"]["desconhecidos"] = ["XX000000000BR"]
    calls["register"].clear()
    await svc.run(db, pedidos=[p])
    assert calls["register"] == []


async def test_pull_so_reregistra_pedido_ainda_em_devolucao(db: AsyncSession, fakes):
    """Pedido que já saiu de Aguardando Devolução (e pacote já entregue, que o
    17track apaga) não pode voltar pro registro — seria crédito à toa."""
    calls, respostas = fakes
    vivo, morto, chegou = (f"9{uuid4().hex[:6]}" for _ in range(3))
    db.add_all([
        DevolucaoRastreio(pedido_bling=vivo, rastreio_auto="AP111111111BR"),
        DevolucaoRastreio(pedido_bling=morto, rastreio_auto="AP222222222BR"),
        DevolucaoRastreio(
            pedido_bling=chegou, rastreio_auto="AP333333333BR",
            localizacao_auto="Piracicaba/SP — Objeto entregue ao destinatário",
            localizacao_auto_data=datetime.now(UTC),
        ),
    ])
    await db.commit()
    respostas["17track"]["desconhecidos"] = ["AP111111111BR", "AP222222222BR", "AP333333333BR"]

    resumo = await svc._puxar_correios(db, vivos=[vivo, chegou])

    assert resumo["desconhecidos"] == ["AP111111111BR"]
    # O entregue nem foi perguntado: a prova já estava no texto guardado.
    assert resumo["consultados"] == 2 and resumo["entregues"] == 1


def test_helpers_de_data():
    # 1787872539 = create_time real da devolução do 291869 (TikTok, 27/08 20:15 BRT).
    assert epoch_to_dt(1787872539) == datetime(2026, 8, 27, 23, 15, 39, tzinfo=UTC)
    assert epoch_to_dt(1787872539000) == datetime(2026, 8, 27, 23, 15, 39, tzinfo=UTC)
    assert epoch_to_dt(None) is None and epoch_to_dt("x") is None and epoch_to_dt(0) is None
    assert iso_to_dt("2026-09-03T10:42:47Z") == datetime(2026, 9, 3, 10, 42, 47, tzinfo=UTC)
    assert iso_to_dt("2026-09-03T07:42:47-03:00") == datetime(2026, 9, 3, 10, 42, 47, tzinfo=UTC)
    assert iso_to_dt("") is None and iso_to_dt(None) is None
