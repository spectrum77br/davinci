"""17track × Amazon Envio próprio: o pacote entra na varredura mesmo fora de
"Em andamento", a leitura "Delivered" carimba a entrega e a ocorrência grave
carimba `problema_correios` (o que dispara a mensagem ao cliente). Busca por
pedido (sem Redis)."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Logistica
from app.services import logistica_track, logistica_track_sync


@pytest.fixture
def fake_17track(monkeypatch):
    estado = {"eventos": []}
    chamadas = {"register": [], "fetch": []}

    async def _register(numbers):
        chamadas["register"].append(list(numbers))
        return {"ok": list(numbers), "sem_quota": False}

    async def _fetch(numbers):
        chamadas["fetch"].append(list(numbers))
        return list(estado["eventos"])

    async def _sem_aviso(graves):
        chamadas.setdefault("graves", []).append(graves)

    monkeypatch.setattr(logistica_track, "register", _register)
    monkeypatch.setattr(logistica_track, "fetch", _fetch)
    monkeypatch.setattr(logistica_track_sync, "avisar_graves", _sem_aviso)
    return chamadas, estado


def _amazon(**kw) -> Logistica:
    base = {
        "pedido_bling": "296762",
        "pedido_marketplace": "701-3967231-6921832",
        "plataforma": "Amazon",
        "conta": "kia",
        "data": date.today(),
        "rastreio": "AD912266053BR",
        "amazon_canal": "proprio",
        "status_bling": "Em digitação",
        "meli_status": {"order_status": "Shipped", "fulfillment_channel": "MFN"},
    }
    base.update(kw)
    return Logistica(**base)


def test_na_varredura_amazon_em_qualquer_situacao_nao_final():
    assert logistica_track_sync._na_varredura(_amazon()) is True
    assert logistica_track_sync._na_varredura(_amazon(status_bling="Entregue")) is False
    assert logistica_track_sync._na_varredura(_amazon(status_bling="Cancelado")) is False
    from datetime import UTC, datetime

    assert logistica_track_sync._na_varredura(_amazon(entregue_em=datetime.now(UTC))) is False
    # ML continua exigindo "Em andamento".
    ml = _amazon(plataforma="Mercado Livre", status_bling="Problemas")
    assert logistica_track_sync._na_varredura(ml) is False


@pytest.mark.asyncio
async def test_delivered_carimba_entrega_e_grave_carimba_problema(db: AsyncSession, fake_17track):
    chamadas, estado = fake_17track
    db.add(_amazon())
    await db.commit()

    estado["eventos"] = [("AD912266053BR", "Gararu/SE — Objeto em trânsito", "InTransit")]
    out = await logistica_track_sync.run(db, pedidos=["296762"])
    assert out["registrados"] == 1 and out["atualizados"] == 1
    row = (await db.execute(select(Logistica))).scalars().one()
    assert row.localizacao == "Gararu/SE — Objeto em trânsito" and row.entregue_em is None

    # O pull só reconsulta quem não tem leitura há PULL_APOS_HORAS (o tempo
    # real é o push do webhook): envelhece a leitura pra simular a rodada seguinte.
    from datetime import UTC, datetime, timedelta

    row.localizacao_at = datetime.now(UTC) - timedelta(hours=7)
    await db.commit()
    estado["eventos"] = [
        ("AD912266053BR", "Gararu/SE — Objeto entregue ao destinatário", "Delivered")
    ]
    await logistica_track_sync.run(db, pedidos=["296762"])
    await db.refresh(row)
    assert row.entregue_em is not None
    assert row.problema_correios is None

    # Fora da varredura automática depois de entregue (mas a busca por pedido segue).
    assert logistica_track_sync._na_varredura(row) is False


@pytest.mark.asyncio
async def test_evento_grave_carimba_problema_uma_vez(db: AsyncSession, fake_17track):
    chamadas, estado = fake_17track
    db.add(_amazon())
    await db.commit()
    estado["eventos"] = [("AD912266053BR", "Aracaju/SE — Objeto extraviado", "Exception")]
    await logistica_track_sync.run(db, pedidos=["296762"])
    row = (await db.execute(select(Logistica))).scalars().one()
    assert row.problema_correios == "Aracaju/SE — Objeto extraviado"
    primeiro = row.problema_correios_em
    assert primeiro is not None
    assert chamadas["graves"] and chamadas["graves"][0][0][0] == "296762"

    # Leitura nova, ainda grave: mantém o primeiro carimbo (uma mensagem só).
    estado["eventos"] = [
        ("AD912266053BR", "Aracaju/SE — Objeto extraviado — em análise", "Exception")
    ]
    await logistica_track_sync.run(db, pedidos=["296762"])
    await db.refresh(row)
    assert row.problema_correios_em == primeiro


def test_aplicar_leitura_eventos_no_formato_antigo_continuam_valendo():
    # Fake antigo devolve 2-tuplas: nada quebra.
    r = _amazon()
    assert logistica_track_sync.aplicar_leitura(r, "X — Y") is True
    assert r.localizacao == "X — Y" and r.entregue_em is None
    assert logistica_track_sync.aplicar_leitura(r, "X — Y") is False
