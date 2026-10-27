# ruff: noqa: S105, S106  (tokens de teste, nada real)
"""Zap — credenciais e os GETs do cadastro (RF10, Onda 1) — docs/zap/desenho.md §3.1 e §10.1.

A Graph API é de MENTIRA (`httpx.MockTransport`): nenhum teste fala com a
Meta, e a chamada não prevista reprova.

- `inspecionar_token` ESTRITA: recusa token inválido, de outro app, sem um dos
  escopos do WhatsApp ou sem a WABA nos `granular_scopes`; `expires_at=0` =
  nunca vence (explícito); a Meta fora do ar é ERRO (nunca "sem validade");
  o token nunca aparece no erro (nem o `input_token=` da URL);
- o token vai no HEADER; a versão é a do Zap (`meta_whatsapp_graph_version`);
  redirecionamento não é seguido;
- `ler_waba`, `listar_numeros` (todas as páginas) e `descobrir_numeros`;
- o segredo do app e o token da conta cifrados em repouso; o cache do
  webhook com a janela do segredo anterior.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.config import get_settings
from app.models import AtendimentoZapApp, AtendimentoZapConta
from app.security.cipher import decrypt_json
from app.services.atendimento import zap_api, zap_contas

TOKEN = "EAAzapTOKENdeMENTIRA0000000000000000"
APP_ID = "910000000000001"
WABA = "920000000000001"


class Graph:
    def __init__(self) -> None:
        self.rotas: dict[tuple[str, str], Any] = {}
        self.chamadas: list[httpx.Request] = []

    def quando(self, metodo: str, caminho: str, resposta: Any) -> None:
        self.rotas[(metodo, caminho)] = resposta

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.chamadas.append(request)
        resposta = self.rotas.get((request.method, request.url.path))
        if resposta is None:
            raise AssertionError(f"chamada não prevista: {request.method} {request.url.path}")
        if isinstance(resposta, Exception):
            raise resposta
        if callable(resposta):
            resposta = resposta(request)
        if isinstance(resposta, httpx.Response):
            return resposta
        status, corpo = resposta if isinstance(resposta, tuple) else (200, resposta)
        return httpx.Response(status, json=corpo)

    def cliente(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


@pytest.fixture
def graph(monkeypatch):
    g = Graph()
    monkeypatch.setattr(zap_api, "novo_cliente", g.cliente)
    monkeypatch.setattr(get_settings(), "meta_whatsapp_graph_version", "v26.0")
    return g


def _debug(**dados) -> dict:
    base = {
        "app_id": APP_ID,
        "type": "SYSTEM_USER",
        "is_valid": True,
        "expires_at": 0,
        "scopes": ["whatsapp_business_messaging", "whatsapp_business_management"],
        "granular_scopes": [
            {"scope": "whatsapp_business_messaging", "target_ids": [WABA]},
            {"scope": "whatsapp_business_management", "target_ids": [WABA]},
        ],
        "user_id": "61500000000001",
    }
    base.update(dados)
    return {"data": base}


async def test_inspecao_estrita_aceita_o_token_certo(graph):
    graph.quando("GET", "/v26.0/debug_token", _debug())
    insp = await zap_api.inspecionar_token(TOKEN, app_id=APP_ID, waba_id=WABA)
    assert insp.nunca_vence is True and insp.expira_em is None
    assert insp.system_user_id == "61500000000001"
    (req,) = graph.chamadas
    # O token vai no header; na URL só o `input_token` que o debug_token exige.
    assert req.headers["authorization"] == f"OAuth {TOKEN}"
    assert req.url.params["input_token"] == TOKEN
    assert "access_token" not in req.url.params


async def test_inspecao_sem_target_ids_aceita_e_avisa(graph):
    # O que a Meta devolveu em 27/10/2026 para o "davinciteste" COM a WABA de
    # teste atribuída: os escopos do WhatsApp sem `target_ids`. Não recusa; a
    # conferência da WABA fica com quem chamou (`alvos_detalhados=False`).
    graph.quando(
        "GET",
        "/v26.0/debug_token",
        _debug(
            granular_scopes=[
                {"scope": "whatsapp_business_management"},
                {"scope": "whatsapp_business_messaging"},
                {"scope": "manage_app_solution"},
            ]
        ),
    )
    insp = await zap_api.inspecionar_token(TOKEN, app_id=APP_ID, waba_id=WABA)
    assert insp.alvos_detalhados is False
    graph.quando("GET", "/v26.0/debug_token", _debug())
    assert (await zap_api.inspecionar_token(TOKEN, app_id=APP_ID, waba_id=WABA)).alvos_detalhados


async def test_inspecao_com_validade(graph):
    vence = int((datetime.now(UTC) + timedelta(days=60)).timestamp())
    graph.quando("GET", "/v26.0/debug_token", _debug(expires_at=vence))
    insp = await zap_api.inspecionar_token(TOKEN, app_id=APP_ID, waba_id=WABA)
    assert insp.nunca_vence is False
    assert insp.expira_em == datetime.fromtimestamp(vence, tz=UTC)


@pytest.mark.parametrize(
    ("dados", "trecho"),
    [
        ({"is_valid": False}, "não é válido"),
        ({"app_id": "910000000000999"}, "OUTRO app"),
        ({"scopes": ["whatsapp_business_messaging"]}, "whatsapp_business_management"),
        (
            {
                "granular_scopes": [
                    {"scope": "whatsapp_business_messaging", "target_ids": ["999"]},
                    {"scope": "whatsapp_business_management", "target_ids": [WABA]},
                ]
            },
            "não enxerga esta conta",
        ),
        ({"granular_scopes": []}, "não enxerga esta conta"),
        ({"expires_at": None}, "quando o token vence"),
    ],
)
async def test_inspecao_estrita_recusa(graph, dados, trecho):
    graph.quando("GET", "/v26.0/debug_token", _debug(**dados))
    with pytest.raises(zap_api.ZapApiErro) as e:
        await zap_api.inspecionar_token(TOKEN, app_id=APP_ID, waba_id=WABA)
    assert trecho in str(e.value)
    assert TOKEN not in str(e.value)


async def test_meta_fora_do_ar_e_erro_sem_o_token_na_mensagem(graph):
    graph.quando("GET", "/v26.0/debug_token", httpx.ConnectTimeout("timeout em ...?input_token=x"))
    with pytest.raises(zap_api.ZapApiErro) as e:
        await zap_api.inspecionar_token(TOKEN, app_id=APP_ID, waba_id=WABA)
    assert "não respondeu" in str(e.value) and TOKEN not in str(e.value)
    assert zap_api.eh_ambiguo(e.value)
    # Erro com código da Meta (que às vezes ecoa o token): traduzido e mascarado.
    graph.quando(
        "GET",
        "/v26.0/debug_token",
        (400, {"error": {"code": 190, "message": f"Invalid OAuth access token {TOKEN}"}}),
    )
    with pytest.raises(zap_api.ZapApiErro) as e:
        await zap_api.inspecionar_token(TOKEN, app_id=APP_ID, waba_id=WABA)
    assert e.value.code == 190 and "token novo" in str(e.value)
    assert TOKEN not in str(e.value)
    assert not zap_api.eh_ambiguo(e.value)


async def test_redirecionamento_nao_e_seguido(graph):
    graph.quando(
        "GET",
        f"/v26.0/{WABA}",
        httpx.Response(302, headers={"location": "https://outro-host.invalid/roubo"}),
    )
    with pytest.raises(zap_api.ZapApiErro):
        await zap_api.ler_waba(WABA, TOKEN)
    assert [r.url.host for r in graph.chamadas] == ["graph.facebook.com"]


async def test_versao_e_a_do_zap_nao_a_do_instagram(graph, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "meta_graph_version", "v99.0")
    graph.quando("GET", f"/v26.0/{WABA}", {"id": WABA, "name": "Conta"})
    assert (await zap_api.ler_waba(WABA, TOKEN))["name"] == "Conta"
    monkeypatch.setattr(s, "meta_whatsapp_graph_version", "v27.0")
    graph.quando("GET", f"/v27.0/{WABA}", {"id": WABA, "name": "Conta 27"})
    assert (await zap_api.ler_waba(WABA, TOKEN))["name"] == "Conta 27"


async def test_listar_numeros_todas_as_paginas_e_descobrir(graph):
    paginas = iter(
        [
            {
                "data": [{"id": "930000000000001", "display_phone_number": "+55 11 90000-0001"}],
                "paging": {"cursors": {"after": "C1"}, "next": "https://graph.facebook.com/x"},
            },
            {
                "data": [
                    {
                        "id": "930000000000002",
                        "display_phone_number": "+55 11 90000-0002",
                        "verified_name": "Charlots",
                        "name_status": "APPROVED",
                        "quality_rating": "GREEN",
                    }
                ],
                "paging": {"cursors": {"after": "C2"}},
            },
        ]
    )
    vistos: list[str | None] = []

    def _pagina(req: httpx.Request) -> httpx.Response:
        vistos.append(req.url.params.get("after"))
        return httpx.Response(200, json=next(paginas))

    graph.quando("GET", f"/v26.0/{WABA}/phone_numbers", _pagina)
    conta = AtendimentoZapConta(waba_id=WABA, token_enc=zap_contas.cifrar_token_conta(TOKEN))
    achados = await zap_contas.descobrir_numeros(conta)
    assert vistos == [None, "C1"]
    assert [a["phone_number_id"] for a in achados] == ["930000000000001", "930000000000002"]
    assert achados[1] == {
        "phone_number_id": "930000000000002",
        "display_phone_number": "+55 11 90000-0002",
        "verified_name": "Charlots",
        "nome_status": "APPROVED",
        "quality_rating": "GREEN",
    }
    assert all(r.method == "GET" for r in graph.chamadas)  # só leitura


# ─────────────── cifra e cache ───────────────


def test_segredos_cifrados_em_repouso():
    blob = zap_contas.cifrar_segredo_app("segredo-do-app")
    assert b"segredo-do-app" not in blob
    assert decrypt_json(blob) == {"atual": "segredo-do-app", "anterior": None, "anterior_ate": None}
    conta = AtendimentoZapConta(waba_id=WABA, token_enc=zap_contas.cifrar_token_conta(TOKEN))
    assert TOKEN.encode() not in conta.token_enc
    assert zap_contas.token_da_conta(conta) == TOKEN
    assert decrypt_json(conta.token_enc)["tipo"] == "system_user"
    with pytest.raises(ValueError):
        zap_contas.cifrar_segredo_app("  ")
    with pytest.raises(ValueError):
        zap_contas.cifrar_token_conta("")
    assert len(zap_contas.gerar_verify_token()) >= 40


def test_troca_de_segredo_rotina_e_vazamento():
    app = AtendimentoZapApp(app_id=APP_ID, segredo_enc=zap_contas.cifrar_segredo_app("velho"))
    agora = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    zap_contas.trocar_segredo(app, "novo", motivo="rotina", agora=agora)
    s = decrypt_json(app.segredo_enc)
    assert (s["atual"], s["anterior"]) == ("novo", "velho")
    assert datetime.fromisoformat(s["anterior_ate"]) == agora + timedelta(hours=1)
    zap_contas.trocar_segredo(app, "novissimo", motivo="vazou", agora=agora)
    s = decrypt_json(app.segredo_enc)
    assert (s["atual"], s["anterior"], s["anterior_ate"]) == ("novissimo", None, None)


def test_anterior_valido_so_dentro_da_janela():
    agora = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    seg = zap_contas.SegredosApp(
        id=None,
        app_id=APP_ID,
        teste=True,
        atual=b"novo",
        anterior=b"velho",
        anterior_ate=agora + timedelta(hours=1),
    )
    assert seg.anterior_valido(agora) == b"velho"
    assert seg.anterior_valido(agora + timedelta(hours=1, seconds=1)) is None
    # O `repr` nunca leva segredo (log, exceção).
    assert "velho" not in repr(seg) and "novo" not in repr(seg)
