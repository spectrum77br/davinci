"""Logística › `entregue_em` = hora do EVENTO de entrega dos Correios.

16/11 (Eduardo): o DaVinci gravava a hora em que LEU a entrega no 17track.
Pedido 306097 (Amazon KIA, AE071697531BR): DaVinci 29/10 11:01 (reconsulta
forçada) × Correios 28/10 15:23. Agora vale a data do evento "Delivered" que
o 17track manda (push E pull); sem data, a hora da leitura; nunca no futuro.

Sem banco nem Redis (17track e sessão falsos): roda com `--noconftest`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.models import Logistica
from app.services import logistica_track, logistica_track_sync

BRT = ZoneInfo("America/Sao_Paulo")
NUM = "AE071697531BR"
# O caso do pedido 306097.
EVENTO_306097 = datetime(2026, 10, 28, 15, 23, tzinfo=BRT)
LEITURA_306097 = datetime(2026, 10, 29, 11, 1, 21, tzinfo=BRT)
LOC_ENTREGUE = "SAO PAULO/SAO PAULO, SP — Objeto entregue ao destinatário"


def _ti_entregue(**tempo) -> dict:
    """track_info v2.2 de um pacote entregue; `tempo` = campos de data do evento."""
    return {
        "latest_status": {"status": "Delivered"},
        "latest_event": {
            "description": "Objeto entregue ao destinatário",
            "location": "SP",
            "address": {"city": "SAO PAULO/SAO PAULO, SP"},
            **tempo,
        },
    }


def _push(track_info: dict, number: str = NUM) -> dict:
    return {"event": "TRACKING_UPDATED", "data": {"number": number, "track_info": track_info}}


def _linha(**kw) -> Logistica:
    base = {
        "pedido_bling": "306097",
        "plataforma": "Amazon",
        "conta": "kia",
        "data": date(2026, 10, 26),
        "rastreio": NUM,
        "rastreio_17track": NUM,
        "amazon_canal": "proprio",
        "status_bling": "Em andamento",
        "meli_status": {"order_status": "Shipped", "fulfillment_channel": "MFN"},
    }
    base.update(kw)
    return Logistica(**base)


# ---- de onde sai a hora do evento ------------------------------------------


def test_quando_entregue_le_o_latest_event_v22():
    ti = _ti_entregue(time_iso="2026-10-28T15:23:00-03:00", time_utc="2026-10-28T18:23:00Z")
    assert logistica_track.quando_entregue(ti) == EVENTO_306097


@pytest.mark.parametrize("aninhado", [True, False])
def test_quando_entregue_pega_o_evento_mais_novo_dos_providers(aninhado):
    eventos = [
        {"time_iso": "2026-10-28T09:10:00-03:00", "description": "Objeto saiu para entrega"},
        {"time_iso": "2026-10-28T15:23:00-03:00", "description": "Objeto entregue"},
        {"time_iso": "2026-10-27T18:00:00-03:00", "description": "Objeto em transferência"},
    ]
    providers = [{"events": eventos}]
    ti = {"latest_status": {"status": "Delivered"}}
    ti |= {"tracking": {"providers": providers}} if aninhado else {"providers": providers}
    assert logistica_track.quando_entregue(ti) == EVENTO_306097


def test_quando_entregue_so_responde_com_delivered():
    ti = _ti_entregue(time_iso="2026-10-28T15:23:00-03:00")
    ti["latest_status"] = {"status": "InTransit"}
    assert logistica_track.quando_entregue(ti) is None
    ti["latest_status"] = {"status": "Expired"}
    assert logistica_track.quando_entregue(ti) is None


def test_quando_entregue_sem_data_no_payload_e_none():
    assert logistica_track.quando_entregue(_ti_entregue()) is None
    assert logistica_track.quando_entregue({"latest_status": {"status": "Delivered"}}) is None
    assert logistica_track.quando_entregue(None) is None


def test_fuso_e_o_mesmo_do_cartao_de_rastreio():
    """A data sai do MESMO `_quando` do cartão de rastreio: `time_iso` com o
    offset dos Correios, senão `time_utc` (com ou sem o "Z", é UTC)."""
    so_utc = _ti_entregue(time_utc="2026-10-28T18:23:00Z")
    assert logistica_track.quando_entregue(so_utc) == EVENTO_306097
    utc_sem_z = _ti_entregue(time_utc="2026-10-28T18:23:00")
    assert logistica_track.quando_entregue(utc_sem_z) == EVENTO_306097
    # `time_iso` manda quando os dois vêm.
    ambos = _ti_entregue(time_iso="2026-10-28T15:23:00-03:00", time_utc="2026-10-29T00:00:00Z")
    assert logistica_track.quando_entregue(ambos) == EVENTO_306097
    # Data ilegível não derruba nada: sem data → None (cai na hora da leitura).
    assert logistica_track.quando_entregue(_ti_entregue(time_iso="ontem")) is None


# 16/11 (Eduardo, revisão): o que vem SEM offset é hora de Brasília (a hora
# local dos Correios), não UTC — lido como UTC, o `entregue_em` (que não muda
# depois de gravado) ficava 3 h mais cedo, e "só a data" virava 21:00 do dia
# anterior. A trava "nunca no futuro" não pegava: o erro jogava pra TRÁS.


@pytest.mark.parametrize(
    "tempo",
    [
        {"time_iso": "2026-10-28T15:23:00"},
        {"time_raw": {"date": "2026-10-28", "time": "15:23:00", "timezone": None}},
        {"time_raw": {"date": "2026-10-28", "time": "15:23", "timezone": ""}},
        {"time_raw": {"date": "2026-10-28", "time": "15:23:00", "timezone": "-03:00"}},
        # fuso que não encaixa no ISO: vale a hora, lida como Brasília
        {"time_raw": {"date": "2026-10-28", "time": "15:23:00", "timezone": "GMT-3"}},
    ],
)
def test_sem_offset_e_hora_de_brasilia_nao_utc(tempo):
    assert logistica_track.quando_entregue(_ti_entregue(**tempo)) == EVENTO_306097


def test_time_raw_respeita_o_fuso_que_vem_nele():
    ti = _ti_entregue(time_raw={"date": "2026-10-28", "time": "18:23:00", "timezone": "+00:00"})
    assert logistica_track.quando_entregue(ti) == EVENTO_306097


@pytest.mark.parametrize(
    "tempo",
    [
        {"time_raw": {"date": "2026-10-28", "time": None, "timezone": None}},
        {"time_raw": {"date": "2026-10-28", "time": "", "timezone": "-03:00"}},
        {"time_iso": "2026-10-28"},
    ],
)
def test_so_a_data_fica_no_dia_certo_em_brasilia(tempo):
    quando = logistica_track.quando_entregue(_ti_entregue(**tempo))
    assert quando == datetime(2026, 10, 28, 0, 0, tzinfo=BRT)
    assert quando.astimezone(BRT).date() == date(2026, 10, 28)  # não 27/10 21:00


def test_campo_com_fuso_vale_mais_que_o_sem_fuso():
    # `time_iso` sem offset + `time_utc`: o UTC é certo, o outro é palpite.
    ti = _ti_entregue(time_iso="2026-10-28T09:00:00", time_utc="2026-10-28T18:23:00Z")
    assert logistica_track.quando_entregue(ti) == EVENTO_306097
    # `time_iso` só com a data + `time_raw` com a hora (sem fuso): vale a hora.
    ti = _ti_entregue(time_iso="2026-10-28", time_raw={"date": "2026-10-28", "time": "15:23:00"})
    assert logistica_track.quando_entregue(ti) == EVENTO_306097


def test_cartao_de_rastreio_mostra_a_hora_sem_offset_como_brasilia():
    """O `_quando` é o mesmo do cartão ao comprador (logistica_cliente_mensagens
    mostra em America/Sao_Paulo): sem offset também não pode sair 3 h antes."""
    ti = {
        "providers": [
            {"events": [{"time_iso": "2026-10-28T15:23:00", "description": "Objeto entregue"}]}
        ]
    }
    (ev,) = logistica_track.eventos_de_track_info(ti)
    assert ev.quando.astimezone(BRT).strftime("%d/%m %H:%M") == "28/10 15:23"


# ---- push (webhook) ---------------------------------------------------------


def test_parse_push_entregas_traz_a_hora_do_evento():
    com_data = _push(_ti_entregue(time_iso="2026-10-28T15:23:00-03:00"))
    assert logistica_track.parse_push_entregas(com_data) == {NUM: EVENTO_306097}
    sem_data = _push(_ti_entregue())
    assert logistica_track.parse_push_entregas(sem_data) == {NUM: None}
    em_transito = _push({"latest_status": {"status": "InTransit"}})
    assert logistica_track.parse_push_entregas(em_transito) == {}
    # O contrato antigo (conjunto de números) continua igual.
    assert logistica_track.parse_push_entregues(com_data) == {NUM}
    assert logistica_track.parse_push_entregues(sem_data) == {NUM}


def _aplica_push(row: Logistica, payload: dict, agora: datetime) -> None:
    """O que o webhook (routers/logistica_track) faz com cada linha."""
    entregas = logistica_track.parse_push_entregas(payload)
    for number, loc in logistica_track.parse_push(payload):
        logistica_track_sync.aplicar_leitura(
            row,
            loc,
            entregue=number in entregas,
            entregue_evento_em=entregas.get(number),
            agora=agora,
        )


def test_caso_306097_push_grava_a_hora_dos_correios():
    row = _linha(localizacao="SAO PAULO, SP — Objeto saiu para entrega ao destinatário")
    _aplica_push(row, _push(_ti_entregue(time_iso="2026-10-28T15:23:00-03:00")), LEITURA_306097)
    assert row.entregue_em == EVENTO_306097
    assert row.entregue_em.astimezone(BRT).strftime("%d/%m %H:%M") == "28/10 15:23"
    # A localização continua com o carimbo da LEITURA (é o "robô vivo" da tela).
    assert row.localizacao_at == LEITURA_306097


def test_caso_306097_push_sem_offset_grava_a_hora_de_brasilia():
    row = _linha()
    so_raw = _ti_entregue(time_raw={"date": "2026-10-28", "time": "15:23:00", "timezone": None})
    _aplica_push(row, _push(so_raw), LEITURA_306097)
    assert row.entregue_em.astimezone(BRT).strftime("%d/%m %H:%M") == "28/10 15:23"


def test_push_sem_data_do_evento_cai_na_hora_da_leitura():
    row = _linha()
    _aplica_push(row, _push(_ti_entregue()), LEITURA_306097)
    assert row.entregue_em == LEITURA_306097


def test_push_com_data_no_futuro_vira_agora():
    agora = datetime.now(UTC)
    futuro = (agora + timedelta(days=2)).isoformat()
    row = _linha()
    _aplica_push(row, _push(_ti_entregue(time_iso=futuro)), agora)
    assert row.entregue_em == agora


def test_entrega_e_carimbada_uma_vez_so():
    row = _linha()
    _aplica_push(row, _push(_ti_entregue(time_iso="2026-10-28T15:23:00-03:00")), LEITURA_306097)
    depois = _push(_ti_entregue(time_iso="2026-10-30T10:00:00-03:00"))
    _aplica_push(row, depois, LEITURA_306097 + timedelta(days=2))
    assert row.entregue_em == EVENTO_306097


class _Resultado:
    def __init__(self, rows: list):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


@pytest.mark.asyncio
async def test_rota_do_webhook_grava_a_hora_do_evento(monkeypatch):
    """A rota de verdade (routers/logistica_track), com sessão e Redis falsos."""
    import json
    from types import SimpleNamespace

    from app.routers import logistica_track as rota

    async def _primeira_vez(_hash):
        return True

    monkeypatch.setattr(rota, "_claim_push", _primeira_vez)
    segredo = "segredo-de-teste"  # noqa: S105 — segredo de teste, não é segredo
    monkeypatch.setattr(
        rota, "get_settings", lambda: SimpleNamespace(logi_17track_webhook_secret=segredo)
    )
    row = _linha(localizacao="SAO PAULO, SP — Objeto saiu para entrega ao destinatário")

    class _Sessao(_SessaoFalsa):
        async def execute(self, stmt):
            entidade = stmt.column_descriptions[0]["entity"]
            return _Resultado([row] if entidade is Logistica else [])

    corpo = json.dumps(_push(_ti_entregue(time_iso="2026-10-28T15:23:00-03:00"))).encode()

    class _Req:
        async def body(self):
            return corpo

    sessao = _Sessao()
    out = await rota.receive_17track_push(segredo, _Req(), sessao)
    assert out == {"ack": True, "numbers": 1, "rows": 1}
    assert row.entregue_em == EVENTO_306097
    assert sessao.commits == 1


def test_momento_da_entrega():
    agora = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
    assert logistica_track_sync.momento_da_entrega(None, agora) == agora
    assert logistica_track_sync.momento_da_entrega(agora + timedelta(minutes=5), agora) == agora
    assert logistica_track_sync.momento_da_entrega(EVENTO_306097, agora) == EVENTO_306097
    # Sem fuso: UTC (o `_quando` já devolve sempre com fuso; isto é só defesa).
    ingenua = datetime(2026, 10, 28, 18, 23)
    assert logistica_track_sync.momento_da_entrega(ingenua, agora) == EVENTO_306097


def test_aplicar_leitura_sem_entrega_nao_carimba():
    row = _linha()
    logistica_track_sync.aplicar_leitura(
        row, "SP — Objeto em trânsito", entregue=False, entregue_evento_em=EVENTO_306097
    )
    assert row.entregue_em is None


# ---- pull (gettrackinfo) ----------------------------------------------------


class _Resp:
    status_code = 200

    def __init__(self, body: dict):
        self._body = body

    def json(self) -> dict:
        return self._body


def _cliente_17track(track_infos: dict[str, dict]):
    """httpx.AsyncClient falso: o gettrackinfo devolve `track_infos`."""

    class _Cliente:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, headers=None, json=None):
            aceitos = [{"number": n, "track_info": ti} for n, ti in track_infos.items()]
            return _Resp({"code": 0, "data": {"accepted": aceitos, "rejected": []}})

    return _Cliente


class _SessaoFalsa:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_fetch_devolve_a_hora_do_evento_de_entrega(monkeypatch):
    monkeypatch.setattr(
        logistica_track.httpx,
        "AsyncClient",
        _cliente_17track(
            {
                NUM: _ti_entregue(time_iso="2026-10-28T15:23:00-03:00"),
                "AA000000001BR": _ti_entregue(),
                "AA000000002BR": {
                    "latest_status": {"status": "InTransit"},
                    "latest_event": {
                        "time_iso": "2026-10-28T10:00:00-03:00",
                        "description": "Objeto em transferência",
                        "location": "SP",
                    },
                },
            }
        ),
    )
    out = {t[0]: t for t in await logistica_track.fetch([NUM, "AA000000001BR", "AA000000002BR"])}
    assert out[NUM][2] == "Delivered" and out[NUM][3] == EVENTO_306097
    assert out["AA000000001BR"][3] is None  # entregue sem data no payload
    assert out["AA000000002BR"][2] == "InTransit" and out["AA000000002BR"][3] is None


@pytest.mark.asyncio
async def test_caso_306097_pull_grava_a_hora_dos_correios_e_salva(monkeypatch):
    """Reconsulta forçada / pull de 15 min: a localização já era a de entregue
    (nada "atualizado"), mas a entrega é carimbada com a hora do evento E
    salva — a condição antiga comparava `entregue_em == agora`."""
    monkeypatch.setattr(
        logistica_track.httpx,
        "AsyncClient",
        _cliente_17track({NUM: _ti_entregue(time_iso="2026-10-28T15:23:00-03:00")}),
    )
    eventos = await logistica_track.fetch([NUM])
    row = _linha(localizacao=eventos[0][1])
    sessao = _SessaoFalsa()
    atualizados = await logistica_track_sync._aplicar_localizacoes(sessao, [row], eventos)
    assert atualizados == 0
    assert row.entregue_em == EVENTO_306097
    assert sessao.commits == 1


@pytest.mark.asyncio
async def test_pull_sem_data_e_tuplas_antigas_caem_na_hora_da_leitura():
    antes = datetime.now(UTC)
    sem_data = _linha(rastreio="AA000000001BR", rastreio_17track="AA000000001BR")
    antiga = _linha(rastreio="AA000000003BR", rastreio_17track="AA000000003BR")
    sessao = _SessaoFalsa()
    await logistica_track_sync._aplicar_localizacoes(
        sessao,
        [sem_data, antiga],
        [
            ("AA000000001BR", LOC_ENTREGUE, "Delivered", None),
            ("AA000000003BR", LOC_ENTREGUE, "Delivered"),  # formato antigo (3-tupla)
        ],
    )
    assert sem_data.entregue_em is not None and sem_data.entregue_em >= antes
    assert antiga.entregue_em is not None and antiga.entregue_em >= antes
    assert sessao.commits == 1


@pytest.mark.asyncio
async def test_pull_com_data_no_futuro_vira_agora(monkeypatch):
    antes = datetime.now(UTC)
    futuro = (antes + timedelta(days=3)).astimezone(BRT).isoformat()
    monkeypatch.setattr(
        logistica_track.httpx,
        "AsyncClient",
        _cliente_17track({NUM: _ti_entregue(time_iso=futuro)}),
    )
    eventos = await logistica_track.fetch([NUM])
    assert eventos[0][3] is not None and eventos[0][3] > antes  # o fetch traz como veio
    row = _linha()
    sessao = _SessaoFalsa()
    await logistica_track_sync._aplicar_localizacoes(sessao, [row], eventos)
    assert antes <= row.entregue_em <= datetime.now(UTC)  # gravado: nunca no futuro
    assert sessao.commits == 1


@pytest.mark.asyncio
async def test_sem_entrega_nova_nem_localizacao_nova_nao_salva():
    row = _linha(localizacao=LOC_ENTREGUE, entregue_em=EVENTO_306097)
    sessao = _SessaoFalsa()
    await logistica_track_sync._aplicar_localizacoes(
        sessao, [row], [(NUM, LOC_ENTREGUE, "Delivered", EVENTO_306097)]
    )
    assert sessao.commits == 0


@pytest.mark.asyncio
async def test_fetch_detalhado_e_botao_usam_a_hora_do_evento(monkeypatch):
    async def _chamar(c, endpoint, payload, *, base=logistica_track._BASE):
        ti = _ti_entregue(time_iso="2026-10-28T15:23:00-03:00")
        return {"code": 0, "data": {"accepted": [{"number": NUM, "track_info": ti}]}}

    monkeypatch.setattr(logistica_track, "_chamar", _chamar)
    det = await logistica_track.fetch_detalhado([NUM])
    info = det["info"][NUM]
    assert info["status"] == "Delivered" and info["entregue_evento_em"] == EVENTO_306097
    # O botão ⟳ (e o caminho "encerrado" dele) aplica pelo `_aplicar_info`.
    row = _linha()
    logistica_track_sync._aplicar_info(row, info, LEITURA_306097)
    assert row.entregue_em == EVENTO_306097
