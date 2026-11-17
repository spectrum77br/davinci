"""Backfill do `logistica.entregue_em` antigo pela hora do evento (16/11).

O carimbo novo só vale pras entregas depois do deploy; as 724 linhas
Correios que já tinham `entregue_em` (o pedido 306097 entre elas: 29/10 11:01
× entregue 28/10 15:23) só mudam com `scripts/backfill_entregue_em_evento.py`,
que é SECO por padrão e só grava com o OK do Eduardo.

Sem banco (sessão e 17track falsos): roda com `--noconftest`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.models import Logistica
from app.services import logistica_track
from scripts import backfill_entregue_em_evento as bf

BRT = ZoneInfo("America/Sao_Paulo")
NUM = "AE071697531BR"
EVENTO_306097 = datetime(2026, 10, 28, 15, 23, tzinfo=BRT)
LEITURA_306097 = datetime(2026, 10, 29, 11, 1, 21, tzinfo=BRT)


def _info(evento: datetime | None, status: str = "Delivered") -> dict:
    return {"status": status, "entregue_evento_em": evento}


# ---- avaliar (a regra) ------------------------------------------------------


def test_caso_306097_volta_pra_hora_dos_correios():
    assert bf.avaliar(LEITURA_306097, _info(EVENTO_306097)) == ("corrigir", EVENTO_306097)


def test_so_anda_pra_tras():
    # Evento DEPOIS do gravado: fica o gravado (a leitura nunca é antes da entrega).
    assert bf.avaliar(EVENTO_306097, _info(LEITURA_306097)) == ("igual", None)
    # Já é a hora do evento (carimbo novo): nada a fazer.
    assert bf.avaliar(EVENTO_306097, _info(EVENTO_306097)) == ("igual", None)


def test_sem_data_nao_entregue_e_desconhecido_nao_mexem():
    assert bf.avaliar(LEITURA_306097, _info(None)) == ("sem_data", None)
    assert bf.avaliar(LEITURA_306097, _info(EVENTO_306097, "InTransit")) == ("nao_entregue", None)
    assert bf.avaliar(LEITURA_306097, None) == ("desconhecido", None)


def test_recuo_grande_e_suspeita_e_nao_e_gravado():
    antigo = LEITURA_306097 - timedelta(days=20)
    assert bf.avaliar(LEITURA_306097, _info(antigo)) == ("suspeita", antigo)
    assert bf.avaliar(LEITURA_306097, _info(antigo), max_dias=30) == ("corrigir", antigo)


def test_gravado_sem_fuso_e_utc():
    ingenua = LEITURA_306097.astimezone(UTC).replace(tzinfo=None)
    assert bf.avaliar(ingenua, _info(EVENTO_306097)) == ("corrigir", EVENTO_306097)


# ---- rodar (com sessão e 17track falsos) -----------------------------------


class _Resultado:
    def __init__(self, rows: list):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Sessao:
    def __init__(self, rows: list[Logistica]):
        self.rows = rows
        self.commits = 0

    async def execute(self, _stmt):
        return _Resultado(self.rows)

    async def commit(self):
        self.commits += 1


def _linha(pedido: str, rastreio: str, entregue_em: datetime) -> Logistica:
    return Logistica(
        pedido_bling=pedido,
        plataforma="Amazon",
        conta="kia",
        data=date(2026, 10, 26),
        rastreio=rastreio,
        rastreio_17track=rastreio.lower(),  # o número é normalizado como no sync
        entregue_em=entregue_em,
    )


@pytest.fixture
def cenario(monkeypatch):
    linhas = [
        _linha("306097", NUM, LEITURA_306097),
        _linha("300001", "AA000000001BR", LEITURA_306097),  # entregue sem data
        _linha("300002", "AA000000002BR", LEITURA_306097),  # recuo de 20 dias
        _linha("300003", "AA000000003BR", LEITURA_306097),  # 17track não conhece
    ]
    pedidos_ao_17track: list[list[str]] = []

    async def _fetch_detalhado(numeros):
        pedidos_ao_17track.append(list(numeros))
        return {
            "info": {
                NUM: _info(EVENTO_306097),
                "AA000000001BR": _info(None),
                "AA000000002BR": _info(LEITURA_306097 - timedelta(days=20)),
            },
            "desconhecidos": ["AA000000003BR"],
        }

    monkeypatch.setattr(logistica_track, "fetch_detalhado", _fetch_detalhado)
    return linhas, pedidos_ao_17track


@pytest.mark.asyncio
async def test_seco_mostra_e_nao_grava(cenario):
    linhas, pedidos_ao_17track = cenario
    sessao = _Sessao(linhas)
    resumo = await bf.rodar(sessao, gravar=False)
    # Uma leitura só, com os números normalizados (maiúsculas), sem repetir.
    assert pedidos_ao_17track == [sorted([NUM, "AA000000001BR", "AA000000002BR", "AA000000003BR"])]
    assert resumo["seco"] is True
    assert (resumo["corrigir"], resumo["sem_data"], resumo["suspeita"], resumo["desconhecido"]) == (
        1,
        1,
        1,
        1,
    )
    assert resumo["correcoes"] == [
        {
            "pedido": "306097",
            "rastreio": NUM,
            "antes": "29/10/2026 11:01",
            "depois": "28/10/2026 15:23",
            "recuo_h": 19.6,
        }
    ]
    assert [s["pedido"] for s in resumo["suspeitas"]] == ["300002"]
    assert all(r.entregue_em == LEITURA_306097 for r in linhas)
    assert sessao.commits == 0


@pytest.mark.asyncio
async def test_gravar_corrige_so_o_que_e_certo(cenario):
    linhas, _ = cenario
    sessao = _Sessao(linhas)
    resumo = await bf.rodar(sessao, gravar=True)
    assert resumo["seco"] is False and resumo["corrigir"] == 1
    por_pedido = {r.pedido_bling: r.entregue_em for r in linhas}
    assert por_pedido["306097"] == EVENTO_306097
    # Sem data, suspeita e desconhecido ficam como estavam.
    assert por_pedido["300001"] == por_pedido["300002"] == por_pedido["300003"] == LEITURA_306097
    assert sessao.commits == 1


@pytest.mark.asyncio
async def test_gravar_sem_nada_a_corrigir_nao_faz_commit(monkeypatch):
    async def _fetch_detalhado(numeros):
        return {"info": {NUM: _info(EVENTO_306097)}, "desconhecidos": []}

    monkeypatch.setattr(logistica_track, "fetch_detalhado", _fetch_detalhado)
    sessao = _Sessao([_linha("306097", NUM, EVENTO_306097)])
    resumo = await bf.rodar(sessao, gravar=True)
    assert resumo["igual"] == 1 and sessao.commits == 0


def test_argumentos():
    assert bf._argumentos([]).gravar is False
    args = bf._argumentos(["--gravar", "--pedidos", "306097", "300001", "--max-dias", "30"])
    assert args.gravar and args.pedidos == ["306097", "300001"] and args.max_dias == 30
    with pytest.raises(SystemExit):
        bf._argumentos(["--seco", "--gravar"])
    with pytest.raises(SystemExit):
        bf._argumentos(["--max-dias", "0"])
