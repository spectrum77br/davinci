"""O esqueleto do AGENTE de atendimento (Claude Haiku 5.5, 20/10/2026).

O que já está pronto no pacote `services/atendimento/agente/`:

- `contexto`: o escopo, o registro de evidências, o rastro enxuto, o
  orçamento das ferramentas, as ações de pessoa e o `fatos.agente`;
- `sessao`: a sessão de cada ferramenta é só de leitura, com
  `statement_timeout`, e sai com rollback;
- `saida`: a `Proposta` sem valor padrão (o esquema que vai à API tem todos
  os campos em `required`) e a ida dela à `ia.conferir_saida`;
- `custo`: preço por modelo (as duas tabelas do Haiku 5.5, o cache de 5 min
  e de 1 h, o fallback), o teto do dia e da conversa no Redis e o aviso no
  Telegram uma vez por dia;
- `ResultadoAgente`: o rascunho do agente (`prompt_versao="ag1"`);
- os contratos dos módulos da próxima fase (assinaturas que os três agentes
  usam entre si).

Nenhuma chamada a LLM: o SDK real (`anthropic` 1.9.0) roda sobre um
transporte falso do httpx2.
"""

from __future__ import annotations

import inspect
import json
import math
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, get_args
from uuid import uuid4

import anthropic
import httpx2
import pytest
from anthropic.lib.tools import beta_async_tool
from anthropic.types.beta import BetaUsage
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.services.atendimento import agente, constantes, ia
from app.services.atendimento.agente import (
    acao_pessoa,
    checagens,
    contexto,
    custo,
    fatos_loja,
    ferramentas,
    pacote,
    pares,
    prompt,
    saida,
    sessao,
)
from app.services.atendimento.agente.contexto import ContextoAgente
from app.services.atendimento.agente.saida import Proposta

# ─────────────── ajudas ───────────────


def _conversa(**kw: Any) -> SimpleNamespace:
    base = {
        "id": uuid4(),
        "canal_id": uuid4(),
        "integration_id": uuid4(),
        "plataforma": "shopee",
        "canal": "chat",
        "comprador_id": "comprador-1",
        "pedido_marketplace": "251020ABCDEF12",
        "anuncio_id": "58265493964",
        "dados": {},
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _gatilho(**kw: Any) -> SimpleNamespace:
    base = {"id": uuid4(), "enviada_em": datetime(2026, 10, 20, 14, 0, tzinfo=UTC)}
    base.update(kw)
    return SimpleNamespace(**base)


def _ctx(**kw: Any) -> ContextoAgente:
    return ContextoAgente.da_conversa(
        _conversa(**kw), _gatilho(), modo=constantes.MODO_COPILOTO, nomes=("Ana",)
    )


def _proposta(**kw: Any) -> Proposta:
    base = {
        "acao": "responder",
        "texto": "  Olá! O pedido segue normalmente pelo rastreio {rastreio}.  ",
        "assunto": "rastreio",
        "confianca": 0.9,
        "acao_pessoa": None,
        "motivo_pessoa": "",
        "evidencias": ["pedido", " pedido ", "", "ficha:123"],
    }
    base.update(kw)
    return Proposta(**base)


CATEGORIAS = ["rastreio", "garantia", "cancelamento", "confirmar_variacao", "outro"]


class _RedisFalso:
    """O Redis do teto: INCRBYFLOAT, EXPIRE, MGET, GET, SET NX e DELETE."""

    def __init__(self, *, quebrado: bool = False) -> None:
        self.valores: dict[str, str] = {}
        self.ttl: dict[str, int] = {}
        self.quebrado = quebrado

    def _checar(self) -> None:
        if self.quebrado:
            raise ConnectionError("redis fora")

    def pipeline(self, transaction: bool = True) -> _Pipe:
        self._checar()
        return _Pipe(self)

    async def mget(self, chaves: list[str]) -> list[str | None]:
        self._checar()
        return [self.valores.get(c) for c in chaves]

    async def get(self, chave: str) -> str | None:
        self._checar()
        return self.valores.get(chave)

    async def set(self, chave: str, valor: str, *, nx: bool = False, ex: int | None = None):
        self._checar()
        if nx and chave in self.valores:
            return None
        self.valores[chave] = valor
        if ex:
            self.ttl[chave] = ex
        return True

    async def delete(self, *chaves: str) -> int:
        return sum(self.valores.pop(c, None) is not None for c in chaves)


class _Pipe:
    def __init__(self, r: _RedisFalso) -> None:
        self.r = r
        self.ops: list[tuple] = []

    def incrbyfloat(self, chave: str, valor: float) -> _Pipe:
        self.ops.append(("incr", chave, valor))
        return self

    def expire(self, chave: str, ttl: int) -> _Pipe:
        self.ops.append(("expire", chave, ttl))
        return self

    async def execute(self) -> list:
        self.r._checar()
        saida = []
        for op, chave, valor in self.ops:
            if op == "incr":
                novo = float(self.r.valores.get(chave, 0)) + valor
                self.r.valores[chave] = repr(novo)
                saida.append(novo)
            else:
                self.r.ttl[chave] = valor
                saida.append(True)
        return saida


@pytest.fixture
def redis_falso(monkeypatch) -> _RedisFalso:
    r = _RedisFalso()
    monkeypatch.setattr(custo, "redis", r)
    return r


@pytest.fixture
def avisos(monkeypatch) -> list[str]:
    """O Telegram nunca é chamado de verdade: os avisos ficam aqui."""
    enviados: list[str] = []

    async def _avisar(texto: str) -> bool:
        enviados.append(texto)
        return True

    monkeypatch.setattr(custo, "_avisar_telegram", _avisar)
    return enviados


@pytest.fixture
def tetos(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_agente_teto_usd_dia", 5.0)
    monkeypatch.setattr(s, "atendimento_agente_teto_usd_conversa", 0.05)
    return s


# ─────────────── contexto: ids, ações, escopo ───────────────


def test_ids_de_evidencia_tem_o_formato_da_spec():
    assert contexto.ID_PEDIDO == "pedido"
    assert contexto.id_ficha(" 123 ") == "ficha:123"
    assert contexto.id_variacoes("123") == "variacoes:123"
    assert contexto.id_garantia(17) == "garantia:17"
    assert contexto.id_garantia_calculada("12345") == "garantia:pedido:12345"
    assert contexto.id_fato(17) == "fato:17"
    assert contexto.id_regra("reembolso") == "regra:reembolso"
    assert contexto.id_pedido("999") == "pedido:999"
    assert contexto.id_troca("999") == "troca:999"
    assert contexto.id_restricao("1") == "restricao:1"
    assert contexto.id_especificacoes("1") == "espec:1"
    assert contexto.id_modelo("abc") == "modelo:abc"
    assert contexto.id_par(5) == "par:5"


def test_ordenar_acoes_segue_a_regua_e_ignora_codigo_inventado():
    assert contexto.ordenar_acoes(
        ["excecao", "ag_cancelamento", None], ["inventado", "excecao", "segurar_envio"]
    ) == ["ag_cancelamento", "segurar_envio", "excecao"]
    assert contexto.ordenar_acoes() == []


def test_par_de_outro_cliente_nunca_vira_evidencia():
    ctx = _ctx()
    with pytest.raises(ValueError, match="não é prova"):
        ctx.permitir(ids=[contexto.id_par(3)])
    ctx.permitir(ids=["pedido", " ficha:1 ", None, ""], textos=['{"x": 12}', None])
    assert ctx.registro.permite("ficha:1")
    assert ctx.registro.desconhecidas(["pedido", "fato:9", "fato:9", " ", "ficha:1"]) == ["fato:9"]
    assert ctx.permitidos_extra() == ['{"x": 12}']


def test_escopo_inicial_tem_as_chaves_do_pedido_e_o_anuncio():
    ctx = _ctx(
        plataforma="ml",
        pedido_marketplace="2000001",
        dados={"pack_id": "3000001", "pedido_mkt": {"pedido": 2000009}},
    )
    for numero in ("2000001", "#2000001", " 3000001 ", "2000009"):
        assert ctx.pedido_no_escopo(numero), numero
    assert not ctx.pedido_no_escopo("2000002")
    assert not ctx.pedido_no_escopo("")
    assert ctx.anuncio_citado("58265493964")
    assert not ctx.anuncio_citado("1")
    ctx.aceitar_pedido("AB-12", None)
    ctx.aceitar_anuncio("777", "")
    assert ctx.pedido_no_escopo("ab-12")
    assert ctx.anuncio_citado("777")
    # Sem pedido na conversa (pré-venda): escopo vazio.
    assert _ctx(pedido_marketplace=None, anuncio_id=None).pedidos == set()


def test_replay_usa_o_instante_do_gatilho_e_nao_vai_a_loja():
    gatilho = _gatilho()
    ctx = ContextoAgente.da_conversa(
        _conversa(), gatilho, modo=constantes.MODO_AUTO, extra={"modo_replay": True}
    )
    assert ctx.as_of == gatilho.enviada_em
    assert ctx.agora() == gatilho.enviada_em
    assert ctx.sem_ida_a_loja is True
    ao_vivo = _ctx()
    assert ao_vivo.as_of is None and ao_vivo.sem_ida_a_loja is False
    assert abs((ao_vivo.agora() - datetime.now(UTC)).total_seconds()) < 5
    eval_ = ContextoAgente.da_conversa(
        _conversa(),
        gatilho,
        modo=constantes.MODO_AUTO,
        extra={
            "as_of": datetime(2026, 10, 1, tzinfo=UTC),
            "modo_replay": True,
            "sem_ida_a_loja": False,
            "ferramentas_fora": ["buscar_respostas_equipe"],
            "sem_fatos": True,
            "modelo": "claude-sonnet-5-5",
            "effort": "low",
        },
    )
    assert eval_.as_of == datetime(2026, 10, 1, tzinfo=UTC)
    assert eval_.sem_ida_a_loja is False
    assert eval_.ferramentas_fora == frozenset({"buscar_respostas_equipe"})
    assert eval_.sem_fatos and eval_.modelo == "claude-sonnet-5-5" and eval_.effort == "low"


@pytest.mark.parametrize(
    "extra",
    [
        {"desconhecida": 1},
        {"as_of": datetime(2026, 10, 1)},  # sem fuso
        {"max_turnos": True},  # bool não é int aqui
        {"modo_replay": "sim"},
    ],
)
def test_ctx_extra_errado_e_erro_de_programacao(extra):
    with pytest.raises(TypeError):
        contexto.ler_extra(extra)


def test_cortar_resultado_respeita_o_limite_por_resultado_e_por_conversa():
    ctx = _ctx()
    curto = "x" * 100
    assert ctx.cortar_resultado(curto) == curto
    longo = ctx.cortar_resultado("y" * (contexto.MAX_CHARS_RESULTADO * 2))
    assert len(longo) <= contexto.MAX_CHARS_RESULTADO
    assert longo.endswith(contexto.AVISO_CORTE)
    while ctx.chars_de_ferramenta + contexto.MAX_CHARS_RESULTADO <= contexto.MAX_CHARS_FERRAMENTAS:
        ctx.cortar_resultado("z" * contexto.MAX_CHARS_RESULTADO)
    resto = ctx.cortar_resultado("w" * contexto.MAX_CHARS_RESULTADO)
    assert ctx.chars_de_ferramenta <= contexto.MAX_CHARS_FERRAMENTAS
    assert resto.endswith(contexto.AVISO_CORTE) or resto == contexto.SEM_ORCAMENTO
    assert ctx.cortar_resultado("qualquer") == contexto.SEM_ORCAMENTO


def test_rastro_e_enxuto():
    ctx = _ctx()
    passo = ctx.registrar_passo(
        "consultar_manual",
        {"assunto": "garantia", "pergunta": "a" * 1000, "n": 3, "nada": None},
        12.6,
        ids=["regra:garantia", "regra:garantia", *[f"fato:{i}" for i in range(50)]],
    )
    assert passo.ms == 13
    assert len(passo.args["pergunta"]) == contexto.MAX_CHARS_ARG_RASTRO
    assert passo.args["n"] == 3 and passo.args["nada"] is None
    assert len(passo.ids) == contexto.MAX_IDS_RASTRO
    assert passo.ids[0] == "regra:garantia" and passo.ids.count("regra:garantia") == 1
    erro = ctx.registrar_passo("ver_pedido", {"numero": "1"}, 3, erro="não localizado")
    assert erro.para_fatos() == {
        "ferramenta": "ver_pedido",
        "args": {"numero": "1"},
        "ms": 3,
        "ids": [],
        "erro": "não localizado",
    }
    for _ in range(contexto.MAX_PASSOS_RASTRO + 5):
        ctx.registrar_passo("ver_anuncio", {"item_id": "1"}, 1)
    assert len(ctx.rastro) == contexto.MAX_PASSOS_RASTRO


def test_garantias_conferidas_sem_repetir():
    ctx = _ctx()
    assert not ctx.garantia_consultada and ctx.garantias_conferidas == []
    g = {"inicio": "2026-08-01", "fim_hardware": "2026-11-01", "fim_software": "2027-08-01"}
    ctx.registrar_garantias([g, dict(g)])
    ctx.registrar_garantias([])
    assert ctx.garantia_consultada
    assert ctx.garantias_conferidas == [g]


def test_fatos_do_agente_juntam_as_acoes_e_vao_ao_json():
    ctx = _ctx()
    ctx.acoes_codigo = ["segurar_envio"]
    ctx.acoes_modelo = ["excecao", "ag_cancelamento", "nao_existe"]
    ctx.acao = "pessoa"
    ctx.evidencias = ["pedido", "pedido", "fato:2"]
    ctx.parada = agente.PARADA_FIM
    ctx.registrar_passo("ver_anuncio", {"item_id": "1"}, 5, ids=["ficha:1"])
    ctx.uso.somar({"input_tokens": 100, "output_tokens": 50}, "claude-haiku-5-5")
    fatos = ctx.para_fatos()
    json.dumps(fatos)  # vai para o JSONB do rascunho
    assert fatos["versao"] == constantes.PROMPT_VERSAO_AGENTE == "ag1"
    assert fatos["acoes_pessoa"] == ["ag_cancelamento", "segurar_envio", "excecao"]
    assert fatos["acoes_codigo"] == ["segurar_envio"]
    assert fatos["acoes_modelo"] == ["ag_cancelamento", "excecao"]
    assert fatos["evidencias"] == ["pedido", "fato:2"]
    assert fatos["rastro"] == [
        {"ferramenta": "ver_anuncio", "args": {"item_id": "1"}, "ms": 5, "ids": ["ficha:1"]}
    ]
    assert fatos["uso"]["requisicoes"] == 1
    assert fatos["custo_usd"] == pytest.approx((100 * 0.10 + 50 * 0.50) / 1e6)


def test_base_do_gerar_tem_os_campos_do_contrato():
    nomes = [f.name for f in contexto.BaseDoGerar.__dataclass_fields__.values()]
    assert nomes == [
        "recentes",
        "rajada",
        "foco",
        "textos_cliente",
        "textos_rajada",
        "ctx",
        "valores",
        "fatos",
        "fatos_gravados",
        "categorias",
        "regras",
        "sinais",
        "motivos",
        "opcoes_sensiveis",
        "sinais_do_cliente",
        "nomes",
        "modo",
        "forcar",
        "nf_politica_aprovada",
        "insistiu",
    ]
    base = contexto.BaseDoGerar(
        recentes=[],
        rajada=[],
        foco=[],
        textos_cliente=[],
        textos_rajada=[],
        ctx={},
        valores={},
        fatos={},
        fatos_gravados={},
        categorias=[{"id": "garantia"}, {"id": "outro"}],
        regras=[],
        sinais=[
            (constantes.HUMANO_ALERTA, "a"),
            (constantes.HUMANO_ALERTA, "b"),
            (constantes.HUMANO_XINGAMENTO, "c"),
        ],
        motivos=[],
        opcoes_sensiveis=[],
        sinais_do_cliente=[],
        nomes=(),
        modo=constantes.MODO_COPILOTO,
        forcar=False,
        nf_politica_aprovada=False,
        insistiu=False,
    )
    assert base.ids_categorias == ["garantia", "outro"]
    assert base.codigos_dos_sinais == [constantes.HUMANO_ALERTA, constantes.HUMANO_XINGAMENTO]


# ─────────────── sessão só de leitura ───────────────


def _sqlstate(e: BaseException) -> str | None:
    orig = getattr(e, "orig", None)
    causa = getattr(orig, "__cause__", None) or orig
    return getattr(causa, "sqlstate", None)


async def test_sessao_de_leitura_nao_escreve_e_tem_timeout():
    async with sessao.sessao_leitura() as s:
        assert (await s.execute(text("SHOW transaction_read_only"))).scalar() == "on"
        assert (await s.execute(text("SHOW statement_timeout"))).scalar() == "5s"
        assert (await s.execute(text("SELECT 1"))).scalar() == 1
        with pytest.raises(DBAPIError) as erro:
            await s.execute(text("DELETE FROM users"))
    assert _sqlstate(erro.value) == "25006"  # read_only_sql_transaction


async def test_sessao_de_leitura_corta_consulta_lenta():
    with pytest.raises(DBAPIError) as erro:
        async with sessao.sessao_leitura(timeout_ms=50) as s:
            await s.execute(text("SELECT pg_sleep(1)"))
    assert _sqlstate(erro.value) == "57014"  # query_canceled
    # A próxima sessão nasce limpa (o timeout é LOCAL da transação).
    async with sessao.sessao_leitura() as s:
        assert (await s.execute(text("SHOW statement_timeout"))).scalar() == "5s"


# ─────────────── saída: a Proposta ───────────────


def test_proposta_sem_valor_padrao_e_esquema_estrito():
    for nome, campo in Proposta.model_fields.items():
        assert campo.is_required(), nome
    esquema = saida.esquema()
    assert esquema["type"] == "object"
    assert esquema["additionalProperties"] is False
    assert sorted(esquema["required"]) == sorted(
        ["acao", "texto", "assunto", "confianca", "acao_pessoa", "motivo_pessoa", "evidencias"]
    )
    props = esquema["properties"]
    assert props["acao"]["enum"] == ["responder", "perguntar", "pessoa"]
    variantes = props["acao_pessoa"]["anyOf"]
    assert {"type": "null"} in variantes
    assert next(v for v in variantes if v.get("type") == "string")["enum"] == list(
        constantes.ACOES_PESSOA
    )
    assert props["evidencias"]["items"]["type"] == "string"
    assert "default" not in json.dumps(esquema)
    assert get_args(saida.AcaoPessoa) == constantes.ACOES_PESSOA


def test_proposta_nao_aceita_acao_ou_codigo_fora_da_lista():
    assert saida.ler_proposta(json.dumps(_proposta().model_dump())) == _proposta()
    for ruim in (
        _proposta().model_dump() | {"acao": "enviar"},
        _proposta().model_dump() | {"acao_pessoa": "cancelar_pedido"},
        {"acao": "responder"},
    ):
        assert saida.ler_proposta(json.dumps(ruim)) is None
    assert saida.ler_proposta("") is None
    assert saida.ler_proposta('{"acao": "resp') is None


@pytest.mark.parametrize(
    "valor,esperado", [(0.5, 0.5), (1.7, 1.0), (-2, 0.0), (math.nan, 0.0), (math.inf, 0.0)]
)
def test_confianca_entre_zero_e_um(valor, esperado):
    assert saida.confianca(_proposta(confianca=valor)) == esperado


def test_para_conferir_responder_e_perguntar():
    entrada = saida.para_conferir(_proposta(), CATEGORIAS)
    assert entrada == {
        "texto": "Olá! O pedido segue normalmente pelo rastreio {rastreio}.",
        "categoria": "rastreio",
        "categoria_valida": True,
        "precisa_humano": False,
        "motivo_modelo": "",
        "perguntar": False,
    }
    pergunta = saida.para_conferir(
        _proposta(acao="perguntar", assunto="confirmar_variacao", motivo_pessoa="ignorado"),
        CATEGORIAS,
    )
    assert pergunta["perguntar"] and not pergunta["precisa_humano"]
    assert pergunta["categoria"] == "confirmar_variacao" and pergunta["motivo_modelo"] == ""
    assert saida.evidencias(_proposta()) == ["pedido", "ficha:123"]


def test_para_conferir_pessoa_e_acao_declarada():
    pessoa = saida.para_conferir(
        _proposta(acao="pessoa", acao_pessoa="segurar_envio", motivo_pessoa="  não postado  "),
        CATEGORIAS,
    )
    assert pessoa["precisa_humano"]
    assert pessoa["motivo_modelo"] == (
        "ação de pessoa: Segurar o envio (cancelamento antes da postagem) — não postado"
    )
    # Pessoa sem ação da régua: continua pessoa (o lado seguro), e o motivo diz.
    sem_acao = saida.para_conferir(_proposta(acao="pessoa", motivo_pessoa=""), CATEGORIAS)
    assert sem_acao["precisa_humano"]
    assert sem_acao["motivo_modelo"] == saida.MOTIVO_PESSOA_SEM_ACAO
    # Ação declarada numa "responder": o modelo acrescentou uma ação.
    misto = _proposta(acao="responder", acao_pessoa="excecao")
    assert saida.para_conferir(misto, CATEGORIAS)["precisa_humano"]
    assert saida.acoes_do_modelo(misto) == ["excecao"]
    assert saida.acoes_do_modelo(_proposta()) == []


def test_assunto_invalido_vira_outro():
    assert saida.assunto_valido("inventado", CATEGORIAS) == ("outro", True)
    assert saida.assunto_valido(" garantia ", CATEGORIAS) == ("garantia", True)
    # Sem "outro" na lista: fica inválido (a conferir_saida manda para pessoa).
    assert saida.assunto_valido("inventado", ["garantia"]) == ("inventado", False)
    entrada = saida.para_conferir(_proposta(assunto="xpto"), CATEGORIAS)
    assert entrada["categoria"] == "outro" and entrada["categoria_valida"]


def test_para_conferir_casa_com_a_assinatura_da_conferir_saida():
    parametros = inspect.signature(ia.conferir_saida).parameters
    entrada = saida.para_conferir(_proposta(), CATEGORIAS)
    for chave in entrada:
        assert parametros[chave].kind is inspect.Parameter.KEYWORD_ONLY, chave
    assert "agente" in parametros and "permitidos_extra" in parametros
    inspect.signature(ia.conferir_saida).bind(
        None,
        None,
        None,
        **entrada,
        ctx={},
        fatos={},
        valores={},
        categorias=[],
        textos_cliente=[],
        textos_rajada=[],
        garantias_conferidas=[],
        modo=constantes.MODO_COPILOTO,
        agente=True,
    )


# ─────────────── o SDK de verdade sobre um transporte falso ───────────────


def _resposta_api(conteudo: list[dict], parada: str, **uso: int) -> dict:
    return {
        "id": f"msg_{uuid4().hex[:8]}",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-5-5",
        "content": conteudo,
        "stop_reason": parada,
        "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 20, **uso},
    }


async def test_tool_runner_com_a_proposta_e_ferramenta_estrita():
    """O laço como o "cérebro" vai montar: o SDK aceita a `Proposta` e uma
    ferramenta `strict` sem padrão, devolve `parsed_output` no fim, e o
    `usage` de cada turno serve para o custo."""
    pedidos: list[dict] = []
    final = _proposta().model_dump()
    respostas = [
        _resposta_api(
            [
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "ver_anuncio",
                    "input": {"item_id": "9"},
                }
            ],
            "tool_use",
            cache_creation_input_tokens=8000,
            cache_creation={"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 8000},
        ),
        _resposta_api(
            [{"type": "text", "text": json.dumps(final)}],
            "end_turn",
            cache_read_input_tokens=8000,
        ),
    ]

    def atender(request: httpx2.Request) -> httpx2.Response:
        pedidos.append(json.loads(request.content))
        return httpx2.Response(200, json=respostas.pop(0))

    chamadas: list[str | None] = []

    @beta_async_tool(strict=True)
    async def ver_anuncio(item_id: str | None) -> str:
        """O anúncio.

        Args:
            item_id: o id do anúncio.
        """
        chamadas.append(item_id)
        return '<dados fonte="ver_anuncio">{"titulo": "X"}</dados>'

    cliente = anthropic.AsyncAnthropic(
        api_key="sk-ant-test",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(atender)),
        max_retries=0,
    )
    runner = cliente.beta.messages.tool_runner(
        model="claude-haiku-5-5",
        max_tokens=4000,
        max_iterations=5,
        system=[{"type": "text", "text": "sistema", "cache_control": prompt.CACHE_1H}],
        tools=[ver_anuncio],
        messages=[{"role": "user", "content": "<dados>pacote</dados>"}],
        output_format=Proposta,
        output_config={"effort": "medium"},
        cache_control={"type": "ephemeral"},
    )
    uso = custo.Uso()
    ultima = None
    async for msg in runner:
        ultima = msg
        uso.somar(msg.usage, msg.model)
    assert chamadas == ["9"]
    assert ultima is not None and ultima.stop_reason == "end_turn"
    assert ultima.parsed_output == _proposta()
    # O que foi à API: o esquema estrito junto do esforço, a ferramenta
    # estrita com o parâmetro obrigatório, e nada de amostragem/raciocínio.
    corpo = pedidos[0]
    assert corpo["output_config"]["effort"] == "medium"
    assert corpo["output_config"]["format"]["type"] == "json_schema"
    assert corpo["output_config"]["format"]["schema"] == saida.esquema()
    ferramenta = corpo["tools"][0]
    assert ferramenta["strict"] is True
    assert ferramenta["input_schema"]["required"] == ["item_id"]
    for proibido in ("temperature", "top_p", "top_k", "thinking", "fallbacks"):
        assert proibido not in corpo
    assert pedidos[1]["messages"][-1]["content"][0]["type"] == "tool_result"
    # O custo dos dois turnos (gravação de 1 h e leitura do cache).
    assert uso.requisicoes == 2 and uso.cache_1h == 8000 and uso.cache_leitura == 8000
    assert uso.custo_usd == pytest.approx(
        (200 * 0.10 + 40 * 0.50 + 8000 * 0.20 + 8000 * 0.01) / 1e6
    )


# ─────────────── custo ───────────────


def test_tabela_de_preco_do_haiku_muda_acima_de_100_mil():
    curto = custo.preco_do_modelo("claude-haiku-5-5", 100_000)
    longo = custo.preco_do_modelo("claude-haiku-5-5", 100_001)
    assert (curto.entrada, curto.saida, curto.cache_leitura, curto.cache_5m, curto.cache_1h) == (
        0.10,
        0.50,
        0.01,
        0.125,
        0.20,
    )
    assert (longo.entrada, longo.saida) == (0.50, 2.50)
    sonnet = custo.preco_do_modelo("claude-sonnet-5-5", 500_000)
    assert (sonnet.entrada, sonnet.saida, sonnet.cache_1h) == (2.00, 10.00, 4.00)
    assert custo.preco_do_modelo("claude-haiku-5-5-20261015") == curto
    # Desconhecido: o mais caro da tabela (o teto para antes, nunca depois).
    assert custo.preco_do_modelo("claude-xyz") == custo.preco_do_modelo("claude-opus-5")


def test_tokens_do_uso_objeto_do_sdk_e_dicionario():
    usage = BetaUsage(
        input_tokens=1000,
        output_tokens=2000,
        cache_read_input_tokens=8000,
        cache_creation_input_tokens=700,
        cache_creation={"ephemeral_5m_input_tokens": 200, "ephemeral_1h_input_tokens": 500},
        output_tokens_details={"thinking_tokens": 1500},
    )
    t = custo.tokens_do_uso(usage)
    assert (t.entrada, t.saida, t.cache_leitura, t.cache_5m, t.cache_1h, t.pensamento) == (
        1000,
        2000,
        8000,
        200,
        500,
        1500,
    )
    assert t.prompt == 9700
    assert custo.custo_do_uso(usage, "claude-haiku-5-5") == pytest.approx(
        (1000 * 0.10 + 2000 * 0.50 + 8000 * 0.01 + 200 * 0.125 + 500 * 0.20) / 1e6
    )
    # Sem o detalhe por validade: a gravação conta como de 1 h (a mais cara).
    t2 = custo.tokens_do_uso(
        {"input_tokens": 1, "output_tokens": 1, "cache_creation_input_tokens": 9}
    )
    assert (t2.cache_5m, t2.cache_1h) == (0, 9)
    # O detalhe menor que o total: a diferença vai para 1 h.
    t3 = custo.tokens_do_uso(
        {
            "cache_creation_input_tokens": 10,
            "cache_creation": {"ephemeral_5m_input_tokens": 4, "ephemeral_1h_input_tokens": 0},
        }
    )
    assert (t3.cache_5m, t3.cache_1h) == (4, 6)
    assert custo.tokens_do_uso({}) == custo.Tokens(0, 0, 0, 0, 0, 0)


def test_prompt_longo_custa_a_tabela_de_cima():
    usage = {"input_tokens": 120_000, "output_tokens": 1_000}
    assert custo.custo_do_uso(usage, "claude-haiku-5-5") == pytest.approx(
        (120_000 * 0.50 + 1_000 * 2.50) / 1e6
    )


def test_fallback_cobra_cada_iteracao_pelo_seu_modelo():
    usage = {
        "input_tokens": 300,
        "output_tokens": 30,
        "iterations": [
            {"type": "message", "model": "claude-sonnet-5-5", "input_tokens": 100,
             "output_tokens": 10, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
            {"type": "fallback_message", "model": "claude-opus-5-5", "input_tokens": 200,
             "output_tokens": 20, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
        ],
    }  # fmt: skip
    assert custo.custo_do_uso(usage, "claude-sonnet-5-5") == pytest.approx(
        (100 * 2.00 + 10 * 10.00 + 200 * 4.00 + 20 * 20.00) / 1e6
    )


async def test_registrar_soma_dia_e_conversa_com_o_schema_na_chave(redis_falso, avisos, tetos):
    conversa = uuid4()
    uso = custo.Uso()
    agora = datetime(2026, 10, 21, 1, 30, tzinfo=UTC)  # 20/10 em São Paulo
    gasto = await custo.registrar(
        {"input_tokens": 1000, "output_tokens": 1000},
        "claude-haiku-5-5",
        conversa_id=conversa,
        uso=uso,
        agora=agora,
    )
    assert gasto == pytest.approx(0.0006)
    schema = get_settings().database_schema
    dia = f"atd:agente:usd:{schema}:2026-10-20"
    assert float(redis_falso.valores[dia]) == pytest.approx(0.0006)
    assert float(redis_falso.valores[f"{dia}:conversa:{conversa}"]) == pytest.approx(0.0006)
    assert redis_falso.ttl[dia] == custo.TTL_S
    assert uso.requisicoes == 1 and uso.custo_usd == pytest.approx(0.0006)
    assert await custo.gasto_do_dia(agora) == pytest.approx(0.0006)
    await custo.conferir_teto(conversa_id=conversa, uso=uso, agora=agora)
    assert avisos == []


async def test_teto_da_conversa(redis_falso, avisos, tetos):
    conversa = uuid4()
    uso = custo.Uso()
    caro = {"input_tokens": 0, "output_tokens": 60_000}  # US$ 0,03
    await custo.registrar(caro, "claude-haiku-5-5", conversa_id=conversa, uso=uso)
    with pytest.raises(custo.TetoEstourado) as erro:
        await custo.registrar(caro, "claude-haiku-5-5", conversa_id=conversa, uso=uso)
    assert erro.value.qual == custo.TETO_CONVERSA
    assert erro.value.gasto == pytest.approx(0.06) and erro.value.teto == 0.05
    with pytest.raises(custo.TetoEstourado):
        await custo.conferir_teto(conversa_id=conversa)
    # Outra conversa no mesmo dia segue.
    await custo.conferir_teto(conversa_id=uuid4())
    assert avisos == []  # o teto da conversa não avisa no Telegram


async def test_teto_do_dia_para_e_avisa_uma_vez(redis_falso, avisos, tetos):
    tetos.atendimento_agente_teto_usd_dia = 0.01
    caro = {"input_tokens": 0, "output_tokens": 30_000}  # US$ 0,015
    with pytest.raises(custo.TetoEstourado) as erro:
        await custo.registrar(caro, "claude-haiku-5-5", conversa_id=uuid4())
    assert erro.value.qual == custo.TETO_DIA
    with pytest.raises(custo.TetoEstourado):
        await custo.conferir_teto(conversa_id=uuid4())
    with pytest.raises(custo.TetoEstourado):
        await custo.registrar(caro, "claude-haiku-5-5")
    assert len(avisos) == 1
    assert "teto do dia" in avisos[0] and "0.01" in avisos[0]


async def test_aviso_que_nao_saiu_tenta_de_novo(redis_falso, monkeypatch, tetos):
    tetos.atendimento_agente_teto_usd_dia = 0.001
    tentativas: list[str] = []

    async def _falha(texto: str) -> bool:
        tentativas.append(texto)
        return False

    monkeypatch.setattr(custo, "_avisar_telegram", _falha)
    await custo.conferir_teto()  # nada gasto ainda
    assert tentativas == []
    chave = f"atd:agente:usd:{get_settings().database_schema}:{custo.dia_de_hoje()}"
    redis_falso.valores[chave] = "1"
    for _ in range(2):
        with pytest.raises(custo.TetoEstourado):
            await custo.conferir_teto()
    assert len(tentativas) == 2


async def test_teto_zero_e_sem_teto(redis_falso, avisos, tetos):
    tetos.atendimento_agente_teto_usd_dia = 0
    tetos.atendimento_agente_teto_usd_conversa = 0
    uso = custo.Uso()
    for _ in range(3):
        await custo.registrar({"output_tokens": 1_000_000}, "claude-haiku-5-5", uso=uso)
    assert uso.custo_usd == pytest.approx(1.5)
    await custo.conferir_teto(uso=uso)


async def test_redis_fora_nao_para_mas_a_conversa_ainda_tem_teto(monkeypatch, avisos, tetos):
    monkeypatch.setattr(custo, "redis", _RedisFalso(quebrado=True))
    uso = custo.Uso()
    assert await custo.registrar({"output_tokens": 10_000}, "claude-haiku-5-5", uso=uso) > 0
    await custo.conferir_teto(uso=uso)
    with pytest.raises(custo.TetoEstourado) as erro:
        await custo.registrar({"output_tokens": 100_000}, "claude-haiku-5-5", uso=uso)
    assert erro.value.qual == custo.TETO_CONVERSA
    with pytest.raises(custo.TetoEstourado):
        await custo.conferir_teto(uso=uso)
    assert await custo.gasto_do_dia() == 0.0


async def test_registrar_no_redis_de_verdade(avisos, tetos):
    """O pipeline com o `redis.asyncio` real (a chave tem o schema do teste)."""
    from app.redis_client import redis

    conversa = uuid4()
    agora = datetime.now(UTC) + timedelta(days=400)  # um dia que ninguém usa
    dia = custo.dia_de_hoje(agora)
    schema = get_settings().database_schema
    chaves = [
        f"atd:agente:usd:{schema}:{dia}",
        f"atd:agente:usd:{schema}:{dia}:conversa:{conversa}",
    ]
    await redis.delete(*chaves)
    try:
        for _ in range(2):
            await custo.registrar(
                {"input_tokens": 1000, "output_tokens": 0},
                "claude-haiku-5-5",
                conversa_id=conversa,
                agora=agora,
            )
        assert float(await redis.get(chaves[0])) == pytest.approx(0.0002)
        assert float(await redis.get(chaves[1])) == pytest.approx(0.0002)
        assert 0 < await redis.ttl(chaves[0]) <= custo.TTL_S
    finally:
        await redis.delete(*chaves)


# ─────────────── o resultado do agente ───────────────


def test_resultado_vira_rascunho_do_agente():
    ctx = _ctx()
    ctx.acoes_codigo = ["enviar_arquivo_nf"]
    ctx.modelo = "claude-haiku-5-5"
    ctx.uso.somar({"input_tokens": 10, "output_tokens": 5}, "claude-haiku-5-5")
    conversa, gatilho = _conversa(), _gatilho()
    r = agente.ResultadoAgente(
        parada=agente.PARADA_FIM,
        gravar=True,
        texto="Olá!",
        categoria="nota_fiscal",
        confianca=0.9,
        precisa_humano=True,
        motivo="ação de pessoa: Enviar o arquivo da NF",
        validador_ok=True,
        validador_erros=[],
        status=constantes.RASCUNHO_PENDENTE,
        acoes_pessoa=ctx.acoes_pessoa,
        fatos_agente=ctx.para_fatos(),
        modelo="m" * 200,
        manual_hash="abcd",
        tokens_entrada=10,
        tokens_saida=5,
    )
    rascunho = r.para_rascunho(conversa, gatilho, {"lacunas": {}, "agente": "velho"})
    assert rascunho.prompt_versao == "ag1"
    assert rascunho.conversa_id == conversa.id and rascunho.mensagem_gatilho_id == gatilho.id
    assert rascunho.status == constantes.RASCUNHO_PENDENTE
    assert rascunho.fatos["lacunas"] == {}
    assert rascunho.fatos["agente"]["acoes_pessoa"] == ["enviar_arquivo_nf"]
    assert len(rascunho.modelo) == 128
    assert rascunho.validador_erros == [] and rascunho.texto == "Olá!"


def test_resultado_sem_texto_e_sem_rascunho():
    ctx = _ctx()
    ctx.acoes_codigo = ["instrucao"]
    r = agente.ResultadoAgente.sem_texto(
        ctx, parada=agente.PARADA_SEM_AGENTE, motivo=ia.MOTIVO_INJECAO
    )
    assert r.gravar and r.texto is None and r.precisa_humano and not r.validador_ok
    assert r.status == constantes.RASCUNHO_BLOQUEADO and r.validador_erros == ["texto vazio"]
    assert r.fatos_agente["parada"] == agente.PARADA_SEM_AGENTE
    assert r.tokens_entrada is None  # o modelo nem foi chamado
    rascunho = r.para_rascunho(_conversa(), _gatilho(), {})
    assert rascunho.texto is None and rascunho.motivo == ia.MOTIVO_INJECAO
    nada = agente.ResultadoAgente.sem_rascunho(ctx, parada=agente.PARADA_TETO)
    assert not nada.gravar
    with pytest.raises(ValueError):
        nada.para_rascunho(_conversa(), _gatilho(), {})


def test_paradas_cabem_na_coluna_do_replay():
    assert len(set(agente.PARADAS)) == len(agente.PARADAS)
    assert all(len(p) <= 32 for p in agente.PARADAS)


# ─────────────── os contratos da próxima fase ───────────────


def _params(funcao) -> list[str]:
    """Os parâmetros do contrato: os da assinatura MENOS os keyword-only com
    padrão acrescentados no fim (a regra 2 do interfaces.md permite)."""
    parametros = list(inspect.signature(funcao).parameters.values())
    while (
        parametros
        and parametros[-1].kind is inspect.Parameter.KEYWORD_ONLY
        and parametros[-1].default is not inspect.Parameter.empty
        and parametros[-1].name not in _CONTRATO_KW
    ):
        parametros.pop()
    return [p.name for p in parametros]


# Os keyword-only com padrão que JÁ são contrato (não podem sumir).
_CONTRATO_KW = frozenset({"ctx_extra", "agora"})


def test_contrato_das_assinaturas_entre_os_modulos():
    """As funções que um módulo usa do outro (A1 ↔ A2 ↔ A3): nomes fixos. Pode
    acrescentar parâmetro keyword-only com padrão no fim; não pode renomear,
    tirar nem reordenar (interfaces.md, regra 2)."""
    assert _params(agente.responder) == ["session", "conversa", "gatilho", "base", "ctx_extra"]
    assert _params(pacote.montar_pacote) == ["session", "conversa", "gatilho", "base", "ctx"]
    assert _params(pacote.bloco_do_pedido) == [
        "session",
        "ctx_pedido",
        "plataforma",
        "integration_id",
        "conversa_dados",
        "lacunas",
        "agora",
    ]
    assert _params(acao_pessoa.estado_de_postagem) == [
        "session",
        "numero_bling",
        "logistica",
        "agora",
    ]
    assert _params(acao_pessoa.gatilhos_de_codigo) == [
        "session",
        "conversa",
        "gatilho",
        "base",
        "ctx",
        "postado",
    ]
    assert _params(fatos_loja.chaves_ativas) == [
        "session",
        "chaves",
        "plataforma",
        "integration_id",
        "agora",
    ]
    assert _params(ferramentas.ferramentas) == ["ctx", "base", "conversa"]
    assert _params(prompt.sistema) == ["plataforma", "categorias"]
    assert _params(checagens.conferir) == [
        "proposta",
        "conferida",
        "ctx",
        "base",
        "fichas",
        "gatilho",
    ]
    assert _params(checagens.aplicar) == ["conferida", "checagens", "acoes_pessoa", "modo"]
    assert _params(pares.buscar)[:3] == ["session", "pergunta", "plataforma"]

    # O que a regra permite: um keyword-only com padrão no fim não quebra o contrato.
    def _com_extra(proposta, conferida, ctx, base, *, fichas, gatilho, conversa=None):
        return None

    assert _params(_com_extra) == ["proposta", "conferida", "ctx", "base", "fichas", "gatilho"]


def test_constantes_do_contrato():
    assert ferramentas.NOMES == (
        "ver_anuncio",
        "ver_pedido",
        "ver_garantia",
        "consultar_manual",
        "buscar_respostas_equipe",
        "sugerir_troca",
        # Item 6 da rodada "redondo" (23/10/2026): o catálogo da loja, no fim da ordem.
        "buscar_anuncios_da_loja",
    )
    assert set(constantes.ASSUNTOS_ORIENTACAO) <= set(prompt.ORIENTACAO)
    assert {"reclamacao_forte", "marca_autorizacao"} <= set(prompt.ORIENTACAO)
    # As chaves dos fatos que trocam gatilho por orientação são as da semente (0420).
    assert set(acao_pessoa.CHAVES_QUE_TROCAM_GATILHO) == {
        "recusa_desconto",
        "recusa_brinde_amostra",
        "recusa_troca_pagando_diferenca",
        "remetente_etiqueta",
    }
    assert set(pares.PESOS_FONTE) == set(constantes.FONTES_PAR)
    assert acao_pessoa.POSTADOS == ("sim", "nao", "mesmo_dia")
