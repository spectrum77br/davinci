"""Registro automático no 17track + pull da localização dos Correios (Logística).

Eduardo 04/09: "rastreio e localização de correios não está atualizando... isso é
em logística, sempre que mudar precisa atualizar em tempo real também". A causa
era não haver NINGUÉM registrando o rastreio de ENVIO no 17track — sem registro
o 17track não busca nos Correios e nunca empurra evento, então a coluna
Localização ficava eternamente com o proxy do marketplace.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Logistica
from app.redis_client import redis
from app.services import logistica_track, logistica_track_sync


@pytest_asyncio.fixture(autouse=True)
async def _limpa_redis():
    """Quarentena, trava e aviso de saldo vivem no Redis (TTL de horas) — sem
    limpar, um teste contamina o seguinte e a suíte fica dependente da ordem."""

    async def _purga():
        keys = [k async for k in redis.scan_iter("17track:*")]
        if keys:
            await redis.delete(*keys)

    await _purga()
    yield
    await _purga()


@pytest.fixture
def fake_17track(monkeypatch):
    """Substitui as duas chamadas de rede do 17track e grava o que foi pedido."""
    chamadas: dict[str, list] = {"register": [], "fetch": []}
    estado = {"ok": None, "sem_quota": False, "eventos": []}

    async def _register(numbers):
        chamadas["register"].append(list(numbers))
        ok = estado["ok"]
        return {
            "ok": list(numbers) if ok is None else list(ok),
            "sem_quota": estado["sem_quota"],
        }

    async def _fetch(numbers):
        chamadas["fetch"].append(list(numbers))
        return list(estado["eventos"])

    monkeypatch.setattr(logistica_track, "register", _register)
    monkeypatch.setattr(logistica_track, "fetch", _fetch)
    return chamadas, estado


@pytest.mark.asyncio
async def test_registra_rastreio_novo_e_puxa_localizacao(db: AsyncSession, fake_17track):
    chamadas, estado = fake_17track
    row = Logistica(
        pedido_bling="291809",
        plataforma="Mercado Livre",
        data=date.today(),
        rastreio="AD828496989BR",
        localizacao="Aguardando NF → Rio de Janeiro/RJ",
        status_bling="Problemas",
    )
    db.add(row)
    await db.commit()
    estado["eventos"] = [("AD828496989BR", "Rio de Janeiro/RJ — Objeto em trânsito")]

    out = await logistica_track_sync.run(db, pedidos=["291809"])

    assert chamadas["register"] == [["AD828496989BR"]]
    assert out["registrados"] == 1
    assert out["atualizados"] == 1
    await db.refresh(row)
    assert row.rastreio_17track == "AD828496989BR"
    assert row.rastreio_17track_at is not None
    assert row.localizacao == "Rio de Janeiro/RJ — Objeto em trânsito"
    assert row.localizacao_at is not None


@pytest.mark.asyncio
async def test_nao_registra_de_novo_o_mesmo_numero(db: AsyncSession, fake_17track):
    chamadas, _ = fake_17track
    row = Logistica(
        pedido_bling="291810",
        data=date.today(),
        rastreio="AA111111111BR",
        rastreio_17track="AA111111111BR",
    )
    db.add(row)
    await db.commit()

    await logistica_track_sync.run(db, pedidos=["291810"])
    assert chamadas["register"] == []  # já registrado: não gasta quota de novo
    assert chamadas["fetch"] == [["AA111111111BR"]]


@pytest.mark.asyncio
async def test_rastreio_trocado_registra_o_novo(db: AsyncSession, fake_17track):
    chamadas, _ = fake_17track
    row = Logistica(
        pedido_bling="291811",
        data=date.today(),
        rastreio="BB222222222BR",
        rastreio_17track="AA111111111BR",  # o marketplace trocou o código
    )
    db.add(row)
    await db.commit()

    await logistica_track_sync.run(db, pedidos=["291811"])
    assert chamadas["register"] == [["BB222222222BR"]]
    await db.refresh(row)
    assert row.rastreio_17track == "BB222222222BR"


@pytest.mark.asyncio
async def test_sem_quota_nao_marca_como_registrado(db: AsyncSession, fake_17track):
    _, estado = fake_17track
    estado["ok"] = []
    estado["sem_quota"] = True
    row = Logistica(pedido_bling="291812", data=date.today(), rastreio="CC333333333BR")
    db.add(row)
    await db.commit()

    out = await logistica_track_sync.run(db, pedidos=["291812"])
    assert out["sem_quota"] is True
    assert out["registrados"] == 0
    await db.refresh(row)
    # Sem marca: a próxima rodada tenta de novo assim que a quota voltar.
    assert row.rastreio_17track is None


@pytest.mark.asyncio
async def test_ignora_nao_correios_encerrados_e_antigos(db: AsyncSession, fake_17track):
    chamadas, _ = fake_17track
    db.add_all(
        [
            # Não é Correios (não termina em BR).
            Logistica(pedido_bling="a1", data=date.today(), rastreio="42314700000000"),
            # Caso encerrado: o pacote não interessa mais.
            Logistica(
                pedido_bling="a2", data=date.today(), rastreio="DD444444444BR",
                status_bling="Cancelado",
            ),
            # Fora da janela de 90 dias.
            Logistica(
                pedido_bling="a3",
                data=date.today() - timedelta(days=200),
                rastreio="EE555555555BR",
            ),
            # Este vale.
            Logistica(
                pedido_bling="a4", data=date.today(), rastreio="FF666666666BR",
                status_bling="Entregue",
            ),
        ]
    )
    await db.commit()

    await logistica_track_sync.run(db)
    assert chamadas["register"] == [["FF666666666BR"]]


@pytest.mark.asyncio
async def test_erro_do_17track_nao_derruba_o_job(db: AsyncSession, monkeypatch):
    async def _boom(numbers):
        raise RuntimeError("17track fora do ar")

    monkeypatch.setattr(logistica_track, "register", _boom)
    monkeypatch.setattr(logistica_track, "fetch", _boom)
    db.add(Logistica(pedido_bling="291813", data=date.today(), rastreio="GG777777777BR"))
    await db.commit()

    out = await logistica_track_sync.run(db, pedidos=["291813"])
    assert out["registrados"] == 0
    assert out["atualizados"] == 0


def test_register_separa_ok_ja_registrado_e_sem_quota():
    # O /register devolve os problemas dentro de `rejected`: "já registrado" é
    # sucesso pra nós; "quota" tem que aparecer como sem_quota pra ninguém
    # marcar o número e o operador ser avisado.
    data = {
        "accepted": [{"number": "AA111111111BR"}],
        "rejected": [
            {"number": "BB222222222BR", "error": {"code": logistica_track.ERRO_JA_REGISTRADO}},
            {"number": "CC333333333BR", "error": {"code": logistica_track.ERRO_SEM_QUOTA}},
        ],
    }
    assert logistica_track._erro_code(data["rejected"][1]) == logistica_track.ERRO_SEM_QUOTA
    assert logistica_track._erro_code({"number": "x"}) is None


# ---- guarda da localização: proxy do marketplace × físico dos Correios ----


@pytest.mark.asyncio
async def test_proxy_do_ml_atualiza_enquanto_nao_ha_evento_dos_correios(
    db: AsyncSession, monkeypatch
):
    """O proxy do ML tem que continuar atualizando até o 17track dar o físico.

    A guarda antiga (`... and row.localizacao`) bloqueava o próprio ML: a
    primeira frase que ele escrevia na coluna passava a valer como "físico" e
    congelava. Foi o que travou o 291809 em "Aguardando NF → Rio de Janeiro/RJ ·
    previsão 25/08" por 10 dias. Agora a prova de físico é `localizacao_at`.
    """
    from app.services import logistica_meli

    async def _enr(client, order_id):
        return {
            "meli_status": {"order_status": "paid", "ship_status": "shipped"},
            "rastreio": "AD828496989BR",
            "localizacao": "Em trânsito → Rio de Janeiro/RJ",
            "datas": {},
        }

    monkeypatch.setattr(logistica_meli, "build_enrichment", _enr)
    monkeypatch.setattr(
        logistica_meli, "_ml_integration_for_conta", lambda *a, **k: _async_none()
    )

    row = Logistica(
        pedido_bling="291809",
        plataforma="Mercado Livre",
        conta="kia",
        pedido_marketplace="2000014649358745",
        data=date.today(),
        rastreio="AD828496989BR",
        localizacao="Aguardando NF → Rio de Janeiro/RJ · previsão 25/08",
    )
    db.add(row)
    await db.commit()

    await logistica_meli.enrich_row(db, row, client_cache={"kia": object()})
    assert row.localizacao == "Em trânsito → Rio de Janeiro/RJ"

    # Depois que o 17track carimbou o físico, o proxy do ML não sobrescreve mais.
    row.localizacao = "Rio de Janeiro/RJ — Objeto saiu para entrega"
    row.localizacao_at = datetime.now(UTC)
    await logistica_meli.enrich_row(db, row, client_cache={"kia": object()})
    assert row.localizacao == "Rio de Janeiro/RJ — Objeto saiu para entrega"
    await db.commit()


async def _async_none():
    return None


@pytest.mark.asyncio
async def test_entrega_velha_sai_do_alvo_mas_busca_pontual_pega(db: AsyncSession, fake_17track):
    """Entregue há muito tempo não gasta saldo — 344 dos 401 rastreios Correios
    da base estão entregues, e o 17track para de rastrear ~15 dias depois."""
    chamadas, _ = fake_17track
    antiga = date.today() - timedelta(days=30)
    db.add(
        Logistica(
            pedido_bling="b1",
            data=antiga,
            rastreio="HH888888888BR",
            status_bling="Entregue",
            meli_status={"ship_status": "delivered"},
        )
    )
    # Entregue ONTEM ainda vale (é onde a divergência ML × físico aparece).
    db.add(
        Logistica(
            pedido_bling="b2",
            data=date.today() - timedelta(days=1),
            rastreio="II999999999BR",
            status_bling="Entregue",
            meli_status={"ship_status": "delivered"},
        )
    )
    await db.commit()

    await logistica_track_sync.run(db)
    assert chamadas["register"] == [["II999999999BR"]]

    # Mas se o operador for atrás daquele pedido específico, responde.
    chamadas["register"].clear()
    await logistica_track_sync.run(db, pedidos=["b1"])
    assert chamadas["register"] == [["HH888888888BR"]]


@pytest.mark.asyncio
async def test_numero_recusado_entra_em_quarentena(db: AsyncSession, fake_17track):
    """Recusa que não é falta de saldo (formato/transportadora) não pode voltar
    à fila a cada 15 min — senão `pendentes` nunca converge."""
    chamadas, estado = fake_17track
    estado["ok"] = []  # o 17track recusou, e não foi por saldo
    db.add(Logistica(pedido_bling="c1", data=date.today(), rastreio="JJ000000000BR"))
    await db.commit()

    await logistica_track_sync.run(db)
    assert chamadas["register"] == [["JJ000000000BR"]]

    chamadas["register"].clear()
    await logistica_track_sync.run(db)
    assert chamadas["register"] == []  # em quarentena, não reenvia


@pytest.mark.asyncio
async def test_sem_quota_marca_aviso_para_a_tela(db: AsyncSession, fake_17track):
    _, estado = fake_17track
    estado["ok"] = []
    estado["sem_quota"] = True
    db.add(Logistica(pedido_bling="d1", data=date.today(), rastreio="KK111111111BR"))
    await db.commit()

    await logistica_track_sync.run(db)
    assert await logistica_track_sync.sem_quota_desde()

    # Rodada seguinte com saldo apaga o aviso.
    estado["sem_quota"] = False
    estado["ok"] = None
    await logistica_track_sync.run(db, pedidos=["d1"])
    assert await logistica_track_sync.sem_quota_desde() is None
