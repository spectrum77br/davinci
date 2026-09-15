"""Botão ⟳ da Localização (logistica_track_sync.atualizar_linha): registra o
número se preciso, força a consulta aos Correios e espera a leitura nova.
17track falso; espera encurtada."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Logistica
from app.services import logistica_track, logistica_track_sync

NUM = "AD912266053BR"


def _linha(**kw) -> Logistica:
    base = {
        "pedido_bling": "296762",
        "plataforma": "Amazon",
        "conta": "kia",
        "data": date.today(),
        "rastreio": NUM,
        "status_bling": "Em digitação",
        "meli_status": {},
    }
    base.update(kw)
    return Logistica(**base)


@pytest.fixture
def fake(monkeypatch):
    """17track falso com estado controlável: `leituras` é a fila do que o
    gettrackinfo devolve a cada chamada (a última se repete)."""
    st = {
        "leituras": [],
        "desconhecidos": [],
        "estado": {"tracking_status": "Tracking", "is_retracked": False},
        "chamadas": [],
    }

    async def _detalhado(numbers):
        st["chamadas"].append("fetch")
        cur = st["leituras"][0] if len(st["leituras"]) == 1 else st["leituras"].pop(0)
        info = {NUM: cur} if cur else {}
        return {"info": info, "desconhecidos": list(st["desconhecidos"])}

    async def _estado(numbers):
        st["chamadas"].append("estado")
        return {NUM: dict(st["estado"])}

    async def _parar_e_retomar(numbers):
        st["chamadas"].append("parar_e_retomar")
        if st["estado"]["is_retracked"]:
            return _res(ja_retomados=list(numbers))
        return _res(retomados=list(numbers))

    async def _retomar_parados(numbers):
        st["chamadas"].append("retomar_parados")
        return _res(retomados=list(numbers))

    async def _reregistrar(numbers):
        st["chamadas"].append("reregistrar")
        return {"ok": list(numbers), "apagados_sem_registro": [], "sem_quota": False}

    async def _register(numbers):
        st["chamadas"].append("register")
        return {"ok": list(numbers), "sem_quota": False}

    async def _marcar(sem_quota):
        pass

    async def _graves(g):
        st["chamadas"].append(("graves", g))

    monkeypatch.setattr(logistica_track, "fetch_detalhado", _detalhado)
    monkeypatch.setattr(logistica_track, "estado_numeros", _estado)
    monkeypatch.setattr(logistica_track, "parar_e_retomar", _parar_e_retomar)
    monkeypatch.setattr(logistica_track, "retomar_parados", _retomar_parados)
    monkeypatch.setattr(logistica_track, "reregistrar", _reregistrar)
    monkeypatch.setattr(logistica_track, "register", _register)
    monkeypatch.setattr(logistica_track_sync, "marcar_sem_quota", _marcar)
    monkeypatch.setattr(logistica_track_sync, "avisar_graves", _graves)
    return st


def _res(**kw):
    base = {"retomados": [], "ja_retomados": [], "nao_registrados": [], "parados": []}
    base.update(kw)
    return base


def _leitura(loc, sync_at, status="InTransit"):
    return {"localizacao": loc, "sync_at": sync_at, "status": status, "sub_status": ""}


@pytest.mark.asyncio
async def test_forca_gratis_e_aplica_leitura_nova(db: AsyncSession, fake):
    row = _linha(rastreio_17track=NUM, localizacao="SP — Objeto postado")
    db.add(row)
    await db.commit()
    antes = datetime.now(UTC) - timedelta(hours=8)
    fake["leituras"] = [
        _leitura("SP — Objeto postado", antes),
        _leitura("SP — Objeto postado", antes),  # ainda não reconsultou
        _leitura("Aracaju/SE — Objeto em trânsito", datetime.now(UTC)),
    ]
    out = await logistica_track_sync.atualizar_linha(db, row, esperar_s=1.0, intervalo_s=0.01)
    assert out["resultado"] == "atualizado" and out["modo"] == "retomado" and out["mudou"]
    assert row.localizacao == "Aracaju/SE — Objeto em trânsito"
    assert row.rastreio_lido_em is not None and row.localizacao_at is not None
    assert "parar_e_retomar" in fake["chamadas"] and "reregistrar" not in fake["chamadas"]


@pytest.mark.asyncio
async def test_registra_quando_o_17track_nao_conhece(db: AsyncSession, fake):
    row = _linha()
    db.add(row)
    await db.commit()
    fake["desconhecidos"] = [NUM]
    fake["leituras"] = [None, _leitura("SP — Objeto postado", datetime.now(UTC))]
    out = await logistica_track_sync.atualizar_linha(db, row, esperar_s=1.0, intervalo_s=0.01)
    assert out["resultado"] == "atualizado" and out["modo"] == "registrado"
    assert row.rastreio_17track == NUM and row.localizacao == "SP — Objeto postado"
    assert "register" in fake["chamadas"]


@pytest.mark.asyncio
async def test_retomar_ja_usado_vira_reregistrar_e_delivered_carimba(db: AsyncSession, fake):
    row = _linha(rastreio_17track=NUM, localizacao="SP — Saiu para entrega")
    db.add(row)
    await db.commit()
    fake["estado"] = {"tracking_status": "Tracking", "is_retracked": True}
    antes = datetime.now(UTC) - timedelta(hours=5)
    fake["leituras"] = [
        _leitura("SP — Saiu para entrega", antes),
        _leitura("SP — Objeto entregue ao destinatário", datetime.now(UTC), status="Delivered"),
    ]
    out = await logistica_track_sync.atualizar_linha(db, row, esperar_s=1.0, intervalo_s=0.01)
    assert out["resultado"] == "atualizado" and out["modo"] == "reregistrado"
    assert row.entregue_em is not None
    assert "reregistrar" in fake["chamadas"]


@pytest.mark.asyncio
async def test_sem_resposta_a_tempo_devolve_consultando(db: AsyncSession, fake):
    row = _linha(rastreio_17track=NUM, localizacao="SP — Objeto postado")
    db.add(row)
    await db.commit()
    antes = datetime.now(UTC) - timedelta(hours=8)
    fake["leituras"] = [_leitura("SP — Objeto postado", antes)]
    out = await logistica_track_sync.atualizar_linha(db, row, esperar_s=0.05, intervalo_s=0.01)
    assert out["resultado"] == "consultando"
    assert row.rastreio_lido_em is not None


@pytest.mark.asyncio
async def test_encerrado_nao_forca(db: AsyncSession, fake):
    row = _linha(rastreio_17track=NUM, localizacao="SP — Objeto postado")
    db.add(row)
    await db.commit()
    fake["leituras"] = [
        {"localizacao": "SP — Objeto entregue ao destinatário", "sync_at": datetime.now(UTC),
         "status": "Delivered", "sub_status": ""}
    ]
    out = await logistica_track_sync.atualizar_linha(db, row, esperar_s=0.05, intervalo_s=0.01)
    assert out["resultado"] == "encerrado"
    assert row.entregue_em is not None and row.localizacao == "SP — Objeto entregue ao destinatário"
    assert "parar_e_retomar" not in fake["chamadas"]


@pytest.mark.asyncio
async def test_sem_rastreio_correios(db: AsyncSession, fake):
    row = _linha(rastreio="TBA123")
    out = await logistica_track_sync.atualizar_linha(db, row, esperar_s=0.05, intervalo_s=0.01)
    assert out["resultado"] == "sem_rastreio_correios"
