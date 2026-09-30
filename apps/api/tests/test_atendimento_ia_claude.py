# ruff: noqa: S105, S106  (chaves de teste de provedores falsos, nada real)
"""A IA do atendimento com a Claude (API nativa da Anthropic, SDK `anthropic`).

NENHUMA chamada sai do teste: o cliente do SDK é o de verdade (é ele que
monta o pedido e traduz os erros), mas com um transporte falso do httpx2 no
lugar da rede; e o `respx` barra qualquer chamada httpx (o caminho do Groq).
O que se confere:

- qual provedor, modelo e chave saem de cada combinação do `.env` — e que
  chave sk-ant- nunca vai para o Groq (nem digitada torta), nem chave do
  Groq para a Anthropic;
- a forma do pedido: sem temperature, prompt de sistema com cache, esforço
  `low`, o `max_tokens` da Claude e o fallback do servidor só no Opus/Fable;
- o cache do prompt da resposta valendo entre lojas (a loja vai no fim);
- a leitura da resposta: só os blocos de texto, recusa (a com o modelo
  reserva no limite é passageira), corte no max_tokens, JSON com texto em
  volta;
- os erros com a mesma semântica do Groq (limite, definitivo, passageiro);
- o uso no formato que o resto do código lê (com as partes do cache).
"""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import httpx2
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AtendimentoRascunho, AtendimentoRegra
from app.services.atendimento import ia
from app.services.atendimento.constantes import RASCUNHO_PENDENTE
from tests.test_atendimento_ia import (
    PEDIDO_MKT,
    RASTREIO,
    _conversa,
    _msg,
    _pedido_completo,
    _saida,
)

GROQ = "https://api.groq.com/openai/v1"
CHAVE_CLAUDE = "sk-ant-api03-chave-de-teste"
CHAVE_GROQ = "gsk_chave-do-groq-de-teste"
URL_CLAUDE = "https://api.anthropic.com/v1/messages?beta=true"


# ─────────────── resposta da API e transporte falso ───────────────


def _resposta(
    texto: str | None = '{"resposta": "ok"}',
    *,
    blocos: list[dict] | None = None,
    parada: str = "end_turn",
    modelo: str = "claude-opus-5",
    uso: dict | None = None,
    detalhes: dict | None = None,
) -> dict:
    """O corpo JSON de uma resposta da Messages API."""
    if blocos is None:
        blocos = [{"type": "thinking", "thinking": "", "signature": "assinatura"}]
        if texto is not None:
            blocos.append({"type": "text", "text": texto})
    return {
        "id": "msg_teste",
        "type": "message",
        "role": "assistant",
        "model": modelo,
        "content": blocos,
        "stop_reason": parada,
        "stop_sequence": None,
        "stop_details": detalhes,
        "usage": uso
        or {
            "input_tokens": 120,
            "output_tokens": 40,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }


def _erro(status: int, mensagem: str = "x", headers: dict | None = None) -> httpx2.Response:
    return httpx2.Response(
        status,
        headers=headers or {},
        json={"type": "error", "error": {"type": "erro_de_teste", "message": mensagem}},
    )


class Anthropic:
    """Faz o papel da API da Anthropic: guarda cada pedido e devolve o combinado."""

    def __init__(self) -> None:
        self.respostas: list[Any] = []
        self.pedidos: list[httpx2.Request] = []

    def responder(self, *itens: Any) -> None:
        """Em ordem: dict (200 com esse corpo), httpx2.Response, ou exceção a levantar."""
        self.respostas = list(itens)

    def _atender(self, request: httpx2.Request) -> httpx2.Response:
        self.pedidos.append(request)
        item = self.respostas.pop(0) if self.respostas else _resposta()
        if isinstance(item, Exception):
            raise item
        if isinstance(item, httpx2.Response):
            return item
        return httpx2.Response(200, json=item)

    @property
    def corpo(self) -> dict:
        return json.loads(self.pedidos[-1].content)

    @property
    def cabecalhos(self) -> httpx2.Headers:
        return self.pedidos[-1].headers


@pytest.fixture
def api(monkeypatch) -> Anthropic:
    """O cliente do SDK de sempre (`_cliente_claude`), com o transporte falso."""
    falsa = Anthropic()
    original = ia._cliente_claude

    def _cliente(p, **_kw):
        return original(
            p, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(falsa._atender))
        )

    monkeypatch.setattr(ia, "_cliente_claude", _cliente)
    # A memória do "fallback recusado" é do processo: cada teste começa limpo.
    monkeypatch.setattr(ia, "_fallback_recusado", set())
    return falsa


@pytest.fixture
def sem_groq():
    """Qualquer chamada httpx (o caminho do Groq) falha o teste."""
    with respx.mock(assert_all_called=False) as router:
        groq = router.post(f"{GROQ}/chat/completions").mock(return_value=httpx.Response(500))
        yield groq


@pytest.fixture
def claude(monkeypatch):
    """IA ligada com a Claude; o DM segue no Groq (é o `.env` de produção)."""
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_ia_ativa", True)
    monkeypatch.setattr(s, "atendimento_auto_ativo", False)
    monkeypatch.setattr(s, "atendimento_ia_modos", "copiloto,auto")
    monkeypatch.setattr(s, "atendimento_ia_teto_diario", 0)
    monkeypatch.setattr(s, "atendimento_llm_base_url", "")
    monkeypatch.setattr(s, "atendimento_llm_model", "claude-opus-5")
    monkeypatch.setattr(s, "atendimento_llm_api_key", CHAVE_CLAUDE)
    monkeypatch.setattr(s, "llm_base_url", GROQ)
    monkeypatch.setattr(s, "llm_model", "openai/gpt-oss-120b")
    monkeypatch.setattr(s, "llm_api_key", CHAVE_GROQ)
    return s


# ─────────────── qual provedor ───────────────


def _escolher(
    *,
    base: str = "",
    modelo: str = "",
    chave: str = "",
    base_dm: str = GROQ,
    modelo_dm: str = "openai/gpt-oss-120b",
    chave_dm: str = CHAVE_GROQ,
) -> ia.Provedor:
    return ia.escolher_provedor(
        base_url=base,
        modelo=modelo,
        chave=chave,
        base_url_dm=base_dm,
        modelo_dm=modelo_dm,
        chave_dm=chave_dm,
    )


GROQ_PADRAO = ia.Provedor(GROQ, "openai/gpt-oss-120b", CHAVE_GROQ, ia.PROVEDOR_OPENAI)


@pytest.mark.parametrize(
    "entrada,esperado",
    [
        # Nada da Claude no .env: o Groq do DM, como sempre.
        ({}, GROQ_PADRAO),
        # Os `atendimento_llm_*` do caminho OpenAI mandam, como antes.
        (
            {"base": "https://llm.invalid/v1", "modelo": "llama-x", "chave": "chave-at"},
            ia.Provedor("https://llm.invalid/v1", "llama-x", "chave-at", ia.PROVEDOR_OPENAI),
        ),
        # Modelo e chave da Claude.
        (
            {"modelo": "claude-opus-5", "chave": CHAVE_CLAUDE},
            ia.Provedor(ia.BASE_CLAUDE, "claude-opus-5", CHAVE_CLAUDE, ia.PROVEDOR_CLAUDE),
        ),
        (
            {"modelo": "claude-sonnet-5", "chave": CHAVE_CLAUDE},
            ia.Provedor(ia.BASE_CLAUDE, "claude-sonnet-5", CHAVE_CLAUDE, ia.PROVEDOR_CLAUDE),
        ),
        # Só a chave sk-ant-: o modelo herdado do DM não é da Claude → o padrão.
        (
            {"chave": CHAVE_CLAUDE},
            ia.Provedor(ia.BASE_CLAUDE, "claude-opus-5", CHAVE_CLAUDE, ia.PROVEDOR_CLAUDE),
        ),
        (
            {"modelo": "llama-3.3-70b-versatile", "chave": CHAVE_CLAUDE},
            ia.Provedor(ia.BASE_CLAUDE, "claude-opus-5", CHAVE_CLAUDE, ia.PROVEDOR_CLAUDE),
        ),
        # Espaço perdido no .env não muda nada.
        (
            {"modelo": " claude-sonnet-5 ", "chave": f" {CHAVE_CLAUDE} "},
            ia.Provedor(ia.BASE_CLAUDE, "claude-sonnet-5", CHAVE_CLAUDE, ia.PROVEDOR_CLAUDE),
        ),
        # Modelo da Claude SEM chave própria: nunca cai na chave do DM (Groq).
        (
            {"modelo": "claude-opus-5"},
            ia.Provedor(ia.BASE_CLAUDE, "claude-opus-5", "", ia.PROVEDOR_CLAUDE),
        ),
        # ...nem numa chave do Groq posta no campo do atendimento.
        (
            {"modelo": "claude-opus-5", "chave": CHAVE_GROQ},
            ia.Provedor(ia.BASE_CLAUDE, "claude-opus-5", "", ia.PROVEDOR_CLAUDE),
        ),
        # ...nem numa sk-ant- que esteja no DM: a da Claude é SÓ a do atendimento.
        (
            {"modelo": "claude-opus-5", "chave_dm": CHAVE_CLAUDE},
            ia.Provedor(ia.BASE_CLAUDE, "claude-opus-5", "", ia.PROVEDOR_CLAUDE),
        ),
        # O endereço do Groq (do DM ou esquecido no do atendimento) nunca vale
        # para a Claude; o ".../v1" do modo compatível também não.
        (
            {"base": GROQ, "modelo": "claude-opus-5", "chave": CHAVE_CLAUDE},
            ia.Provedor(ia.BASE_CLAUDE, "claude-opus-5", CHAVE_CLAUDE, ia.PROVEDOR_CLAUDE),
        ),
        (
            {
                "base": "https://api.anthropic.com/v1/",
                "modelo": "claude-opus-5",
                "chave": CHAVE_CLAUDE,
            },
            ia.Provedor(ia.BASE_CLAUDE, "claude-opus-5", CHAVE_CLAUDE, ia.PROVEDOR_CLAUDE),
        ),
        # Modelo da Claude herdado do DM: Claude, e a chave do DM não serve.
        (
            {"modelo_dm": "claude-sonnet-5"},
            ia.Provedor(ia.BASE_CLAUDE, "claude-sonnet-5", "", ia.PROVEDOR_CLAUDE),
        ),
        # Caminho OpenAI com sk-ant- herdada do DM: a chave não sai para o Groq.
        (
            {"chave_dm": CHAVE_CLAUDE},
            ia.Provedor(GROQ, "openai/gpt-oss-120b", "", ia.PROVEDOR_OPENAI),
        ),
        # Caminho OpenAI apontado para a Anthropic: a chave do Groq não sai para lá.
        (
            {"base": "https://api.anthropic.com/v1"},
            ia.Provedor(
                "https://api.anthropic.com/v1", "openai/gpt-oss-120b", "", ia.PROVEDOR_OPENAI
            ),
        ),
        # A sk-ant- digitada torta no .env (aspas que sobraram, "Bearer " colado,
        # o "<...>" do exemplo da doc), sem a linha do modelo: não é reconhecida
        # como da Claude, mas também não sai para o Groq.
        *(
            (
                {chave_do_campo: torta},
                ia.Provedor(GROQ, "openai/gpt-oss-120b", "", ia.PROVEDOR_OPENAI),
            )
            for torta in (
                f'"{CHAVE_CLAUDE}"',
                f"'{CHAVE_CLAUDE}'",
                f"Bearer {CHAVE_CLAUDE}",
                f"<{CHAVE_CLAUDE}>",
            )
            for chave_do_campo in ("chave", "chave_dm")
        ),
    ],
)
def test_escolher_provedor(entrada, esperado):
    assert _escolher(**entrada) == esperado


def test_provedor_le_o_env(claude, monkeypatch):
    assert ia.provedor() == ia.Provedor(
        ia.BASE_CLAUDE, "claude-opus-5", CHAVE_CLAUDE, ia.PROVEDOR_CLAUDE
    )
    # Sem as linhas da Claude: o Groq do DM, como sempre.
    monkeypatch.setattr(claude, "atendimento_llm_model", "")
    monkeypatch.setattr(claude, "atendimento_llm_api_key", "")
    assert ia.provedor() == GROQ_PADRAO


def test_cliente_do_sdk_sem_nova_tentativa_e_sem_ler_o_ambiente(monkeypatch):
    # O ambiente não desvia a chamada nem troca a chave.
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://outro-lugar.invalid")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-do-ambiente")
    c = ia._cliente_claude(ia.Provedor(ia.BASE_CLAUDE, "claude-opus-5", CHAVE_CLAUDE, "claude"))
    assert str(c.base_url).rstrip("/") == "https://api.anthropic.com"
    assert c.api_key == CHAVE_CLAUDE
    # A nova tentativa por limite é do `_chamar` (uma, com o Retry-After).
    assert c.max_retries == 0
    assert (c.timeout.read, c.timeout.connect) == (45.0, 8.0)


# ─────────────── forma do pedido ───────────────


async def test_pedido_do_opus(claude, api, sem_groq):
    api.responder(_resposta('{"resposta": "oi"}'))

    texto, uso = await ia._chamar_modelo("SISTEMA", "USUARIO", max_tokens=ia.MAX_TOKENS_RESPOSTA)

    assert texto == '{"resposta": "oi"}'
    assert len(api.pedidos) == 1 and not sem_groq.called
    assert str(api.pedidos[0].url) == URL_CLAUDE
    assert api.cabecalhos["x-api-key"] == CHAVE_CLAUDE
    assert "authorization" not in api.cabecalhos
    assert api.cabecalhos["x-stainless-retry-count"] == "0"
    # O fallback do servidor na forma "default", com o beta dessa forma.
    assert api.cabecalhos["anthropic-beta"] == "server-side-fallback-2026-07-01"
    assert api.corpo == {
        "model": "claude-opus-5",
        "max_tokens": ia.MAX_TOKENS_CLAUDE_RESPOSTA,
        "system": [{"type": "text", "text": "SISTEMA", "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": "USUARIO"}],
        "output_config": {"effort": "low"},
        "fallbacks": "default",
    }
    # Nada que a Claude recusa com 400; o raciocínio fica no padrão (adaptativo).
    for proibido in ("temperature", "top_p", "top_k", "thinking"):
        assert proibido not in api.corpo


async def test_pedido_da_classificacao_usa_o_teto_da_claude(claude, api, sem_groq):
    await ia._chamar_modelo("S", "U", max_tokens=ia.MAX_TOKENS_CLASSIFICACAO)
    assert api.corpo["max_tokens"] == ia.MAX_TOKENS_CLAUDE_CLASSIFICACAO == 2000
    # Sem dizer, é o teto da resposta — como no Groq.
    await ia._chamar_modelo("S", "U")
    assert api.corpo["max_tokens"] == ia.MAX_TOKENS_CLAUDE_RESPOSTA == 4000


async def test_pedido_do_sonnet_sem_fallback(claude, api, sem_groq, monkeypatch):
    monkeypatch.setattr(claude, "atendimento_llm_model", "claude-sonnet-5")
    await ia._chamar_modelo("S", "U")
    assert api.corpo["model"] == "claude-sonnet-5"
    assert "fallbacks" not in api.corpo
    assert "anthropic-beta" not in api.cabecalhos
    assert api.corpo["output_config"] == {"effort": "low"}
    assert "temperature" not in api.corpo


def test_fallback_so_no_opus_5_e_no_fable():
    assert ia._com_fallback("claude-opus-5")
    assert ia._com_fallback("claude-opus-5-5")
    assert ia._com_fallback("claude-fable-5-1")
    assert not ia._com_fallback("claude-sonnet-5")
    assert not ia._com_fallback("claude-haiku-4-5")
    assert not ia._com_fallback("claude-opus-4-8")


async def test_beta_do_fallback_recusado_tenta_sem_ele_e_lembra(claude, api, sem_groq):
    api.responder(
        _erro(
            400,
            "Unexpected value(s) `server-side-fallback-2026-07-01` for the "
            "`anthropic-beta` header.",
        ),
        _resposta('{"resposta": "sem fallback"}'),
    )

    texto, _ = await ia._chamar_modelo("S", "U")

    assert texto == '{"resposta": "sem fallback"}'
    assert len(api.pedidos) == 2
    primeiro = json.loads(api.pedidos[0].content)
    assert primeiro["fallbacks"] == "default"
    assert "fallbacks" not in api.corpo and "anthropic-beta" not in api.cabecalhos
    assert api.corpo["output_config"] == {"effort": "low"}  # o resto é o mesmo pedido
    # Lembra no processo: o próximo pedido já vai sem (uma chamada só).
    await ia._chamar_modelo("S", "U")
    assert len(api.pedidos) == 3 and "fallbacks" not in api.corpo


async def test_400_que_nao_e_do_fallback_e_definitivo_sem_nova_tentativa(claude, api, sem_groq):
    api.responder(_erro(400, "messages: text content blocks must be non-empty"))
    with pytest.raises(ia.ErroProvedor) as exc:
        await ia._chamar_modelo("S", "U")
    assert exc.value.definitivo is True and exc.value.limite is False
    assert len(api.pedidos) == 1
    assert ia._fallback_recusado == set()


# ─────────────── leitura da resposta ───────────────


async def test_so_os_blocos_de_texto(claude, api, sem_groq):
    api.responder(
        _resposta(
            blocos=[
                {
                    "type": "fallback",
                    "from": {"model": "claude-opus-5"},
                    "to": {"model": "claude-opus-4-8"},
                },
                {"type": "thinking", "thinking": "", "signature": "a"},
                {"type": "text", "text": '{"categoria": '},
                {"type": "text", "text": '"rastreio"}'},
            ],
            modelo="claude-opus-4-8",
        )
    )
    texto, uso = await ia._chamar_modelo("S", "U")
    assert texto == '{"categoria": "rastreio"}'
    # Quem respondeu de fato (o fallback), não o pedido.
    assert uso["model"] == "claude-opus-4-8"


async def test_recusa_por_politica_e_definitiva(claude, api, sem_groq):
    api.responder(
        _resposta(
            None,
            blocos=[],
            parada="refusal",
            detalhes={"type": "refusal", "category": "cyber", "explanation": None},
        )
    )
    with pytest.raises(ia.ErroProvedor) as exc:
        await ia._chamar_modelo("S", "U")
    assert exc.value.definitivo is True and exc.value.limite is False
    assert "recusou" in exc.value.motivo and "cyber" in exc.value.motivo


async def test_recusa_sem_categoria(claude, api, sem_groq):
    api.responder(_resposta("parcial", parada="refusal"))
    with pytest.raises(ia.ErroProvedor) as exc:
        await ia._chamar_modelo("S", "U")
    assert exc.value.definitivo is True and "sem categoria" in exc.value.motivo


def _recusa_sem_o_reserva() -> dict:
    """A recusa quando o fallback NÃO rodou: o modelo reserva estava no limite
    ou sobrecarregado, e a API diz em quem tentar (`recommended_model`)."""
    return _resposta(
        None,
        blocos=[],
        parada="refusal",
        detalhes={
            "type": "refusal",
            "category": "cyber",
            "explanation": None,
            "recommended_model": "claude-opus-4-8",
        },
    )


async def test_recusa_com_o_reserva_no_limite_e_passageira(claude, api, sem_groq):
    api.responder(_recusa_sem_o_reserva())
    with pytest.raises(ia.ErroProvedor) as exc:
        await ia._chamar_modelo("S", "U")
    # Falta de capacidade passa: nada de sugestão bloqueada para sempre.
    assert exc.value.definitivo is False
    # ...nem de parar a rodada inteira: o limite é só do modelo reserva.
    assert exc.value.limite is False
    assert "claude-opus-4-8" in exc.value.motivo and "cyber" in exc.value.motivo
    assert len(api.pedidos) == 1


async def test_recusa_com_o_reserva_no_limite_o_cron_tenta_de_novo(
    db: AsyncSession, make_user, claude, api, sem_groq
):
    ia.esquecer_limites()
    conversa = await _conversa(db, make_user)
    await _msg(db, conversa, "hackearam minha conta e compraram no meu nome, cadê meu pedido?")
    api.responder(_recusa_sem_o_reserva())

    assert await ia.gerar_pendentes(db) == 0
    rascunhos = (
        (
            await db.execute(
                select(AtendimentoRascunho).where(AtendimentoRascunho.conversa_id == conversa.id)
            )
        )
        .scalars()
        .all()
    )
    # Sem rascunho: bloqueado contaria como "já tratada" e o cron desistiria.
    assert rascunhos == []
    assert not ia._no_limite()

    # O reserva voltou: a rodada seguinte gera.
    api.responder(_resposta(json.dumps(_saida())))
    assert await ia.gerar_pendentes(db) == 1
    assert len(api.pedidos) == 2


async def test_cortada_no_max_tokens_sem_texto_e_passageira(claude, api, sem_groq):
    api.responder(_resposta(None, parada="max_tokens"))  # só o bloco de raciocínio
    with pytest.raises(ia.ErroProvedor) as exc:
        await ia._chamar_modelo("S", "U")
    assert exc.value.definitivo is False and exc.value.limite is False
    assert "cortada" in exc.value.motivo


async def test_cortada_com_texto_segue_para_o_leitor(claude, api, sem_groq):
    # O leitor do JSON decide: cortado = fora do formato = bloqueado.
    api.responder(_resposta('{"resposta": "Olá! Seu ped', parada="max_tokens"))
    texto, _ = await ia._chamar_modelo("S", "U")
    assert texto == '{"resposta": "Olá! Seu ped'
    assert ia.ler_saida(texto) is None


@pytest.mark.parametrize("texto", [None, "", "  \n "])
async def test_resposta_sem_texto(claude, api, sem_groq, texto):
    api.responder(_resposta(texto))
    with pytest.raises(ia.ErroProvedor) as exc:
        await ia._chamar_modelo("S", "U")
    assert exc.value.motivo == "resposta do provedor sem texto"
    assert exc.value.definitivo is False


async def test_uso_no_formato_do_groq_com_as_partes_do_cache(claude, api, sem_groq):
    api.responder(
        _resposta(
            uso={
                "input_tokens": 100,
                "output_tokens": 30,
                "cache_creation_input_tokens": 700,
                "cache_read_input_tokens": 50,
            }
        )
    )
    _, uso = await ia._chamar_modelo("S", "U")
    assert uso == {
        "prompt_tokens": 850,  # tudo o que o modelo leu
        "completion_tokens": 30,
        "model": "claude-opus-5",
        "input_tokens": 100,
        "cache_creation_input_tokens": 700,
        "cache_read_input_tokens": 50,
    }
    # Classificação + resposta no mesmo rascunho: soma tudo.
    total = ia._somar_uso(uso, dict(uso))
    assert (total["prompt_tokens"], total["completion_tokens"]) == (1700, 60)
    assert total["cache_read_input_tokens"] == 100


def test_somar_uso_do_groq_nao_ganha_chaves_da_claude():
    total = ia._somar_uso(
        {"prompt_tokens": 10, "completion_tokens": 2},
        {"prompt_tokens": 5, "completion_tokens": 1, "model": "m"},
    )
    assert total == {"prompt_tokens": 15, "completion_tokens": 3, "model": "m"}


# ─────────────── erros: a mesma semântica do Groq ───────────────


@pytest.mark.parametrize(
    "status,headers,definitivo,limite,espera",
    [
        (429, {"retry-after": "7"}, False, True, 7.0),
        (429, {}, False, True, None),
        (413, {}, False, True, None),
        (400, {}, True, False, None),
        (422, {}, True, False, None),
        (401, {}, False, False, None),
        (403, {}, False, False, None),
        (404, {}, False, False, None),
        (500, {}, False, False, None),
        (529, {}, False, False, None),
    ],
)
async def test_erros_http(claude, api, sem_groq, status, headers, definitivo, limite, espera):
    api.responder(_erro(status, headers=headers))
    with pytest.raises(ia.ErroProvedor) as exc:
        await ia._chamar_modelo("S", "U")
    assert exc.value.motivo == f"provedor devolveu {status}"
    assert (exc.value.definitivo, exc.value.limite, exc.value.espera) == (
        definitivo,
        limite,
        espera,
    )
    # O SDK não tentou de novo por conta própria (nem no 429/5xx).
    assert len(api.pedidos) == 1


@pytest.mark.parametrize(
    "falha,nome",
    [
        (httpx2.ConnectError("recusada"), "APIConnectionError"),
        (httpx2.ReadTimeout("lento"), "APITimeoutError"),
    ],
)
async def test_falha_de_rede(claude, api, sem_groq, falha, nome):
    api.responder(falha)
    with pytest.raises(ia.ErroProvedor) as exc:
        await ia._chamar_modelo("S", "U")
    assert exc.value.motivo == f"falha de rede ({nome})"
    assert exc.value.definitivo is False and exc.value.limite is False
    assert len(api.pedidos) == 1


async def test_limite_da_claude_tenta_de_novo_uma_vez(claude, api, sem_groq, monkeypatch):
    esperas: list[float] = []

    async def _dormir(s: float) -> None:
        esperas.append(s)

    monkeypatch.setattr(ia, "_dormir", _dormir)
    api.responder(_erro(429, headers={"retry-after": "2"}), _resposta('{"resposta": "ok"}'))

    texto, _ = await ia._chamar("S", "U", max_tokens=ia.MAX_TOKENS_RESPOSTA)

    assert texto == '{"resposta": "ok"}'
    assert esperas == [2.0] and len(api.pedidos) == 2


# ─────────────── nenhuma chave vai para o provedor errado ───────────────


async def test_sem_chave_da_claude_nao_chama_nada(claude, api, sem_groq, monkeypatch):
    # Modelo da Claude, chave do Groq no campo do atendimento.
    monkeypatch.setattr(claude, "atendimento_llm_api_key", CHAVE_GROQ)
    with pytest.raises(ia.ErroProvedor) as exc:
        await ia._chamar_modelo("S", "U")
    assert "sem chave da Claude" in exc.value.motivo
    assert api.pedidos == [] and not sem_groq.called
    # A tela diz o porquê, e o cron nem monta o prompt.
    assert ia.provedor().chave == ""

    # Sem chave nenhuma no atendimento: nunca a do DM.
    monkeypatch.setattr(claude, "atendimento_llm_api_key", "")
    with pytest.raises(ia.ErroProvedor):
        await ia._chamar_modelo("S", "U")
    assert api.pedidos == [] and not sem_groq.called


async def test_chave_sk_ant_nunca_vai_para_o_groq(claude, api, sem_groq, monkeypatch):
    # Só a chave da Claude no atendimento; modelo e endereço do Groq no ar.
    monkeypatch.setattr(claude, "atendimento_llm_model", "")
    monkeypatch.setattr(claude, "atendimento_llm_base_url", GROQ)
    await ia._chamar_modelo("S", "U")
    assert not sem_groq.called
    assert str(api.pedidos[0].url) == URL_CLAUDE
    assert api.corpo["model"] == ia.MODELO_CLAUDE_PADRAO

    # A sk-ant- herdada do DM também não sai pelo caminho OpenAI.
    monkeypatch.setattr(claude, "atendimento_llm_api_key", "")
    monkeypatch.setattr(claude, "llm_api_key", CHAVE_CLAUDE)
    with pytest.raises(ia.ErroProvedor) as exc:
        await ia._chamar_modelo("S", "U")
    assert exc.value.motivo == "sem chave do provedor"
    assert not sem_groq.called and len(api.pedidos) == 1

    # Nem digitada torta no campo do atendimento: nada sai, para lugar nenhum.
    monkeypatch.setattr(claude, "llm_api_key", CHAVE_GROQ)
    for torta in (f'"{CHAVE_CLAUDE}"', f"Bearer {CHAVE_CLAUDE}", f"<{CHAVE_CLAUDE}>"):
        monkeypatch.setattr(claude, "atendimento_llm_api_key", torta)
        with pytest.raises(ia.ErroProvedor) as exc:
            await ia._chamar_modelo("S", "U")
        assert exc.value.motivo == "sem chave do provedor", torta
        assert not sem_groq.called and len(api.pedidos) == 1
        # A tela diz "sem chave" e o cron nem monta o prompt.
        assert ia.provedor().chave == ""


async def test_chave_do_groq_nunca_vai_para_a_anthropic(claude, api, monkeypatch):
    # Caminho OpenAI apontado para o endereço da Anthropic, chave do Groq.
    monkeypatch.setattr(claude, "atendimento_llm_model", "")
    monkeypatch.setattr(claude, "atendimento_llm_api_key", "")
    monkeypatch.setattr(claude, "atendimento_llm_base_url", "https://api.anthropic.com/v1")
    with respx.mock(assert_all_called=False) as router:
        anthropic_compat = router.post("https://api.anthropic.com/v1/chat/completions")
        with pytest.raises(ia.ErroProvedor) as exc:
            await ia._chamar_modelo("S", "U")
    assert exc.value.motivo == "sem chave do provedor"
    assert not anthropic_compat.called and api.pedidos == []


async def test_caminho_do_groq_segue_igual(claude, api, monkeypatch):
    """Sem as linhas da Claude, o pedido ao Groq é o de sempre (com temperature)."""
    monkeypatch.setattr(claude, "atendimento_llm_model", "")
    monkeypatch.setattr(claude, "atendimento_llm_api_key", "")
    with respx.mock(assert_all_called=True) as router:
        rota = router.post(f"{GROQ}/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "model": "openai/gpt-oss-120b",
                    "choices": [{"message": {"content": "{}"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
            )
        )
        await ia._chamar_modelo("S", "U", max_tokens=ia.MAX_TOKENS_CLASSIFICACAO)
    corpo = json.loads(rota.calls[0].request.content)
    assert corpo == {
        "model": "openai/gpt-oss-120b",
        "messages": [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}],
        "temperature": 0.2,
        "max_tokens": 300,
    }
    assert rota.calls[0].request.headers["Authorization"] == f"Bearer {CHAVE_GROQ}"
    assert api.pedidos == []


# ─────────────── o JSON que a Claude escreve ───────────────


def test_classificacao_aceita_cerca_e_texto_em_volta():
    ids = ["rastreio", "troca"]
    assert ia.ler_classificacao(
        '```json\n{"categoria": "rastreio", "confianca": 0.9}\n```', ids
    ) == ("rastreio")
    assert (
        ia.ler_classificacao(
            'Aqui está:\n```json\n{"categoria": "troca", "confianca": 0.8}\n```\nAbraço.', ids
        )
        == "troca"
    )


def test_saida_aceita_cerca_e_texto_em_volta():
    corpo = json.dumps(_saida("Olá! Seu pedido {numero_pedido} está a caminho."))
    for texto in (
        f"```json\n{corpo}\n```",
        f"```\n{corpo}\n```",
        f"Segue a resposta:\n```json\n{corpo}\n```\nQualquer dúvida, é só avisar.",
        f"Resposta: {corpo}",
    ):
        s = ia.ler_saida(texto)
        assert s is not None, texto
        assert s.resposta == "Olá! Seu pedido {numero_pedido} está a caminho."
        assert s.categoria == "rastreio"


def test_saida_aceita_texto_em_volta_com_chaves():
    """O prompt manda escrever as lacunas entre chaves: o comentário que a
    Claude põe antes ou depois do JSON pode citar uma ("usei {rastreio}")."""
    corpo = json.dumps(_saida("Olá! Seu pedido {numero_pedido} está a caminho."))
    for texto in (
        f"```json\n{corpo}\n```\nObs.: deixei o código na lacuna {{rastreio}}.",
        f"{corpo}\n\nObs.: usei a lacuna {{numero_pedido}}.",
        f"Usei {{numero_pedido}} na resposta:\n{corpo}",
        f"Usei {{numero_pedido}}:\n```json\n{corpo}\n```\nE {{rastreio}} não coube.",
    ):
        s = ia.ler_saida(texto)
        assert s is not None, texto
        assert s.resposta == "Olá! Seu pedido {numero_pedido} está a caminho."
        assert s.categoria == "rastreio"


def test_classificacao_aceita_texto_em_volta_com_chaves():
    ids = ["rastreio", "troca"]
    for texto in (
        '{"categoria": "rastreio", "confianca": 0.9}\nNota: sem {rastreio} na conversa.',
        'Sem {rastreio} na conversa:\n{"categoria": "rastreio", "confianca": 0.9}',
    ):
        assert ia.ler_classificacao(texto, ids) == "rastreio", texto


@pytest.mark.parametrize(
    "texto",
    [
        # Cortada no max_tokens: continua fora do formato.
        '{"resposta": "Olá! Seu ped',
        '{"resposta": "Olá! Seu pedido {numero_pedido} e',
        # Lacuna solta não é objeto.
        "Usei {numero_pedido} e {rastreio}.",
        '{"categoria": "rastreio"}',  # sem a resposta
        "",
    ],
)
def test_saida_fora_do_formato_continua_recusada(texto):
    assert ia.ler_saida(texto) is None


# ─────────────── o cache do prompt de sistema ───────────────

_LOJA = "Nome interno da loja (NÃO escreva para o cliente): {}."

_REGRAS_CACHE = [
    AtendimentoRegra(tipo="seguranca", quando="SEGURANCA pedirem contato", faca="não passe"),
    AtendimentoRegra(
        tipo="categoria", categoria="rastreio", quando="ASSUNTO rastreio", faca="use {rastreio}"
    ),
    AtendimentoRegra(tipo="categoria", quando="GERAL cliente agradecer", faca="agradeça"),
    AtendimentoRegra(tipo="estilo", quando="ESTILO sempre", faca="assine Equipe"),
]


def _prompt_da_loja(
    conta: str,
    categoria: str | None,
    *,
    correcoes: list[dict] | None = None,
    exemplos: list[dict] | None = None,
) -> str:
    conversa = SimpleNamespace(plataforma="shopee", canal="chat", conta=conta)
    return ia.montar_resposta(
        conversa,
        ia.organizar_manual(_REGRAS_CACHE, categoria),
        exemplos or [],
        correcoes=correcoes,
        categoria=categoria,
    ).sistema


def _system(sistema: str) -> list[dict]:
    return ia.pedido_claude(
        "claude-opus-5", sistema, "U", max_tokens=ia.MAX_TOKENS_RESPOSTA, fallback=False
    )["system"]


@pytest.mark.parametrize("categoria", ["rastreio", None])
def test_cache_da_resposta_vale_entre_lojas(categoria):
    """O cache é por PREFIXO: com o nome da loja no começo do prompt (como vai
    para o Groq), nenhuma loja lia o cache da outra."""
    blocos = {
        "kfa": _system(_prompt_da_loja("kfa", categoria)),
        "injox": _system(
            _prompt_da_loja(
                "injox", categoria, correcoes=[{"categoria": "rastreio", "correcao": "DA INJOX"}]
            )
        ),
    }
    com_cache = {conta: [b for b in bs if "cache_control" in b] for conta, bs in blocos.items()}
    # Regras do código, da plataforma, segurança e manual: iguais nas duas
    # lojas do mesmo canal — o que uma gravou a outra lê.
    assert com_cache["kfa"] and com_cache["kfa"] == com_cache["injox"]
    assert all(b["cache_control"] == {"type": "ephemeral"} for b in com_cache["kfa"])
    assert len(com_cache["kfa"]) <= 4  # a API recusa mais de 4 pontos de cache
    cacheado = "".join(b["text"] for b in com_cache["kfa"])
    assert "SEGURANCA pedirem contato" in cacheado and "GERAL cliente agradecer" in cacheado
    assert ("ASSUNTO rastreio" in cacheado) is (categoria is not None)
    # O que muda por loja fica na cauda: a última, sem cache, depois de todos
    # os pontos de cache.
    for conta, bs in blocos.items():
        assert all(b["text"] for b in bs)
        assert "cache_control" not in bs[-1]
        assert all("cache_control" in b for b in bs[:-1])
        assert _LOJA.format(conta) in bs[-1]["text"]
        assert conta not in cacheado
    assert "DA INJOX" in blocos["injox"][-1]["text"]


@pytest.mark.parametrize("categoria", ["rastreio", None])
def test_blocos_da_claude_sao_o_texto_de_sempre_com_a_loja_no_fim(categoria):
    exemplos = [{"cliente": "oi", "resposta": "olá", "categoria": "rastreio", "fonte": "aprovada"}]
    sistema = _prompt_da_loja(
        "kfa",
        categoria,
        correcoes=[{"categoria": "rastreio", "correcao": "CORRECAO"}],
        exemplos=exemplos,
    )
    loja = _LOJA.format("kfa")
    groq = str(sistema).split("\n\n")
    # O texto (o que vai para o Groq) é o de sempre: abre dizendo quem é a loja.
    assert groq[0].startswith("Você escreve as respostas") and f" {loja}\n" in groq[0]
    claude = "\n\n".join(b["text"] for b in _system(sistema)).split("\n\n")
    # Para a Claude, as mesmas seções na mesma ordem; só a loja mudou de lugar.
    assert claude.count(loja) == 1
    assert [s for s in claude if s != loja] == [groq[0].replace(f" {loja}", ""), *groq[1:]]


def test_prompt_de_um_bloco_so_segue_com_cache():
    # A classificação (igual entre lojas) e quem passa um texto simples.
    assert _system("SISTEMA") == [
        {"type": "text", "text": "SISTEMA", "cache_control": {"type": "ephemeral"}}
    ]


# ─────────────── de ponta a ponta ───────────────


async def test_rascunho_com_a_claude(db: AsyncSession, make_user, claude, api, sem_groq):
    await _pedido_completo(db)
    conversa = await _conversa(db, make_user)
    await _msg(db, conversa, "cadê meu pedido? ainda não chegou", ha=timedelta(minutes=5))
    saida = _saida(
        "Olá! Seu pedido {numero_pedido} foi enviado em {data_envio} pela {transportadora}. "
        "O código de rastreio é {rastreio}."
    )
    api.responder(
        _resposta(
            f"Claro! Segue:\n```json\n{json.dumps(saida, ensure_ascii=False)}\n```",
            uso={
                "input_tokens": 300,
                "output_tokens": 90,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 1500,
            },
        )
    )

    r = await ia.gerar_rascunho(db, conversa)

    assert r is not None and r.status == RASCUNHO_PENDENTE
    assert r.texto == (
        f"Olá! Seu pedido {PEDIDO_MKT} foi enviado em 24/09/2026 pela SEDEX. "
        f"O código de rastreio é {RASTREIO}."
    )
    assert (r.tokens_entrada, r.tokens_saida) == (1800, 90)
    assert r.modelo == "claude-opus-5"
    # Sem regra de assunto: uma chamada só, a da resposta.
    assert len(api.pedidos) == 1 and not sem_groq.called
    assert api.corpo["max_tokens"] == ia.MAX_TOKENS_CLAUDE_RESPOSTA
    assert api.corpo["system"][0]["cache_control"] == {"type": "ephemeral"}
    # O que não sai para o provedor continua não saindo.
    assert RASTREIO not in api.corpo["messages"][0]["content"]


async def test_pedidos_de_duas_lojas_dividem_o_cache(
    db: AsyncSession, make_user, claude, api, sem_groq
):
    c1 = await _conversa(db, make_user, externo_id="c1")
    c2 = await _conversa(db, make_user, externo_id="c2")
    c2.conta = "injox"
    await db.commit()
    for c in (c1, c2):
        await _msg(db, c, "cadê meu pedido? ainda não chegou")
    api.responder(_resposta(json.dumps(_saida())), _resposta(json.dumps(_saida())))

    for c in (c1, c2):
        assert await ia.gerar_rascunho(db, c, forcar=True) is not None

    s1, s2 = (json.loads(p.content)["system"] for p in api.pedidos)
    com_cache = [b for b in s1 if "cache_control" in b]
    assert com_cache and com_cache == [b for b in s2 if "cache_control" in b]
    assert "injox" not in s1[-1]["text"] and _LOJA.format("injox") in s2[-1]["text"]
    assert "cache_control" not in s2[-1]


async def test_recusa_da_claude_grava_bloqueado_com_o_motivo(
    db: AsyncSession, make_user, claude, api, sem_groq
):
    conversa = await _conversa(db, make_user)
    await _msg(db, conversa, "cadê meu pedido?")
    api.responder(
        _resposta(
            None, blocos=[], parada="refusal", detalhes={"type": "refusal", "category": "cyber"}
        )
    )

    r = await ia.gerar_rascunho(db, conversa, forcar=True)

    assert r is not None and r.texto is None and r.precisa_humano is True
    assert "recusou" in (r.motivo or "") and "cyber" in (r.motivo or "")


def test_sdk_instalado_tem_o_que_o_codigo_usa():
    """Se uma versão nova do SDK mudar estes nomes, o teste avisa antes da produção."""
    for nome in (
        "AsyncAnthropic",
        "APIConnectionError",
        "APITimeoutError",
        "APIStatusError",
        "BadRequestError",
        "AnthropicError",
        "Timeout",
    ):
        assert hasattr(anthropic, nome), nome
