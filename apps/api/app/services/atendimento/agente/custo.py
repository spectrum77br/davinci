"""Custo do agente em dólar e o teto de gasto (spec 7.1 e 7.4, 20/10/2026).

Cada requisição ao modelo devolve o `usage` real (SDK `anthropic` 1.9.0,
`types/beta/beta_usage.py`): a entrada em quatro partes — sem cache
(`input_tokens`), gravada no cache (`cache_creation_input_tokens`, que o
`cache_creation` separa em 5 min e 1 h) e lida do cache
(`cache_read_input_tokens`) — e a saída (`output_tokens`, raciocínio
incluso). O preço é por modelo e, no Haiku 5.5, por TAMANHO do prompt: até
100 mil tokens de entrada é uma tabela, acima é outra (cinco vezes mais).

`registrar` soma o custo de cada requisição no Redis, no dia de São Paulo
(`atd:agente:usd:{schema}:{AAAA-MM-DD}`) e na conversa naquele dia
(`atd:agente:usd:{schema}:{AAAA-MM-DD}:conversa:{id}`), e levanta
`TetoEstourado` quando a soma passa do teto. O DIA do agente AO VIVO e o
dos TESTES (o replay da noite, o eval e a calibração) são separados (o dono,
28/10/2026: em 27/10 o eval somou US$ 10 no dia único e o agente ao vivo
parou das 13:06 à meia-noite): os testes somam em `…:{AAAA-MM-DD}:replay`,
com o teto deles (`atendimento_agente_teto_usd_dia_replay`), e nunca comem
o teto do ao vivo (`atendimento_agente_teto_usd_dia`). A CONVERSA do
replay/eval também tem chave própria (`…:replay:conversa:{id}`, revisão de
20/10/2026) e pode ter outro teto (`teto_conversa`: o braço Sonnet do eval
custa ~10× o Haiku). O teto de cada DIA avisa no Telegram uma vez por dia.
A requisição que estourou o tempo não devolve o `usage` (o servidor pode ter
cobrado): `registrar_estimativa` soma o custo provável dela no dia. O schema
na chave é o mesmo cuidado do `ia._CHAVE_TETO`: os testes de cada lote rodam
no seu schema e não somam no dia da produção nem no do outro.

Redis fora do ar não para o agente (o mesmo lado do `ia._dentro_do_teto`):
o freio de fora é o limite mensal do workspace no Console da Anthropic
(spec 7.4) e o teto da conversa ainda vale pelo `Uso` em memória.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog

from app.config import get_settings
from app.redis_client import redis

logger = structlog.get_logger()

# A chave do dia e a da conversa (o schema separa produção e testes); o dia
# dos testes (replay, eval, calibração) é outro (28/10/2026).
CHAVE_DIA = "atd:agente:usd:{schema}:{dia}"
CHAVE_DIA_REPLAY = "atd:agente:usd:{schema}:{dia}:replay"
CHAVE_CONVERSA = "atd:agente:usd:{schema}:{dia}:conversa:{conversa}"
CHAVE_CONVERSA_REPLAY = "atd:agente:usd:{schema}:{dia}:replay:conversa:{conversa}"
# O aviso do teto do dia no Telegram: um por dia (e um do dia dos testes).
CHAVE_AVISO = "atd:agente:usd:{schema}:{dia}:avisado"
CHAVE_AVISO_REPLAY = "atd:agente:usd:{schema}:{dia}:replay:avisado"
TTL_S = 2 * 24 * 3600
SAO_PAULO = ZoneInfo("America/Sao_Paulo")

# Teto <= 0 = sem teto (como o `atendimento_ia_teto_diario`).
TETO_DIA = "dia"
TETO_CONVERSA = "conversa"

# Prompt acima disto (entrada inteira: sem cache + gravada + lida) muda de
# tabela no Haiku 5.5.
LIMITE_PROMPT_CURTO = 100_000


@dataclass(frozen=True)
class Preco:
    """Dólar por milhão de tokens. O cache: leitura e gravação de 5 min e de 1 h."""

    entrada: float
    saida: float
    cache_leitura: float
    cache_5m: float
    cache_1h: float


# Preços de 20/10/2026 (página de preços da Anthropic). Cache: leitura 0,1×
# a entrada (0,05× no Opus 5.5), gravação 1,25× (5 min) e 2× (1 h).
_HAIKU_CURTO = Preco(entrada=0.10, saida=0.50, cache_leitura=0.01, cache_5m=0.125, cache_1h=0.20)
_HAIKU_LONGO = Preco(entrada=0.50, saida=2.50, cache_leitura=0.05, cache_5m=0.625, cache_1h=1.00)
_SONNET = Preco(entrada=2.00, saida=10.00, cache_leitura=0.20, cache_5m=2.50, cache_1h=4.00)
_OPUS_5_5 = Preco(entrada=4.00, saida=20.00, cache_leitura=0.20, cache_5m=5.00, cache_1h=8.00)
_OPUS_5 = Preco(entrada=5.00, saida=25.00, cache_leitura=0.50, cache_5m=6.25, cache_1h=10.00)

# modelo → (tabela até `LIMITE_PROMPT_CURTO`, tabela acima — None = a mesma).
# Item 5 da rodada "redondo" (23/10/2026): o agente no Sonnet 5.5 manda `fallbacks:
# "default"` (`agente._com_fallback`) e a recusa "cyber"/"frontier_llm" é refeita pelo
# servidor no Sonnet 5, que responde com o id dele (`msg.model` e a iteração
# `fallback_message`): mesmo preço do Sonnet 5.5 — fora da tabela ele caía no mais caro.
PRECOS: dict[str, tuple[Preco, Preco | None]] = {
    "claude-haiku-5-5": (_HAIKU_CURTO, _HAIKU_LONGO),
    "claude-sonnet-5-5": (_SONNET, None),
    "claude-sonnet-5": (_SONNET, None),
    "claude-opus-5-5": (_OPUS_5_5, None),
    "claude-opus-5": (_OPUS_5, None),
}
# Modelo fora da tabela: o preço MAIS CARO dela (o teto erra para o lado de
# parar antes, nunca depois), com aviso no log.
_MAIS_CARO = _OPUS_5


class TetoEstourado(Exception):  # noqa: N818 — é um estado do gasto, não bug nosso
    """O gasto passou do teto. `qual` = `TETO_DIA` ou `TETO_CONVERSA`.

    A rodada para (dia) ou a conversa fica sem rascunho (conversa); a Caixa
    Humano a pega. `gasto` e `teto` em dólar.
    """

    def __init__(self, qual: str, gasto: float, teto: float) -> None:
        self.qual = qual
        self.gasto = gasto
        self.teto = teto
        super().__init__(f"teto de gasto do agente ({qual}): US$ {gasto:.4f} de US$ {teto:.2f}")


def _campo(obj: Any, nome: str) -> Any:
    """O campo do `usage` do SDK (objeto) ou do mesmo `usage` já em dicionário."""
    if isinstance(obj, Mapping):
        return obj.get(nome)
    return getattr(obj, nome, None)


def _int(valor: Any) -> int:
    try:
        return max(0, int(valor or 0))
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class Tokens:
    """Os tokens de UMA requisição (ou de uma iteração dela), já separados."""

    entrada: int
    saida: int
    cache_leitura: int
    cache_5m: int
    cache_1h: int
    pensamento: int

    @property
    def prompt(self) -> int:
        """A entrada inteira que o modelo leu (o que escolhe a tabela de preço)."""
        return self.entrada + self.cache_leitura + self.cache_5m + self.cache_1h


def tokens_do_uso(usage: Any) -> Tokens:
    """Separa o `usage` (objeto do SDK ou dicionário). PURA.

    Sem o detalhe do cache por validade (`cache_creation`), a gravação toda
    conta como de 1 h — a mais cara: o teto erra para o lado seguro.
    """
    gravado = _int(_campo(usage, "cache_creation_input_tokens"))
    detalhe = _campo(usage, "cache_creation")
    if detalhe is not None:
        c5m = _int(_campo(detalhe, "ephemeral_5m_input_tokens"))
        c1h = _int(_campo(detalhe, "ephemeral_1h_input_tokens"))
        # O total vale mais que o detalhe quando discordam (nunca cobra menos).
        c1h += max(0, gravado - c5m - c1h)
    else:
        c5m, c1h = 0, gravado
    detalhes_saida = _campo(usage, "output_tokens_details")
    return Tokens(
        entrada=_int(_campo(usage, "input_tokens")),
        saida=_int(_campo(usage, "output_tokens")),
        cache_leitura=_int(_campo(usage, "cache_read_input_tokens")),
        cache_5m=c5m,
        cache_1h=c1h,
        pensamento=_int(_campo(detalhes_saida, "thinking_tokens")) if detalhes_saida else 0,
    )


def preco_do_modelo(modelo: str | None, prompt: int = 0) -> Preco:
    """A tabela do modelo para um prompt deste tamanho. PURA.

    O id exato ou com sufixo de data (`claude-haiku-5-5-2026...`); fora da
    tabela, o mais caro dela.
    """
    nome = (modelo or "").strip().lower()
    tabelas = PRECOS.get(nome)
    if tabelas is None:
        tabelas = next(
            (
                t
                for m, t in sorted(PRECOS.items(), key=lambda x: -len(x[0]))
                if nome.startswith(m + "-")
            ),
            None,
        )
    if tabelas is None:
        logger.warning("atendimento_agente_preco_desconhecido", modelo=nome[:64])
        return _MAIS_CARO
    curto, longo = tabelas
    return longo if longo is not None and prompt > LIMITE_PROMPT_CURTO else curto


def custo_dos_tokens(t: Tokens, modelo: str | None) -> float:
    """Dólar de uma requisição. PURA."""
    p = preco_do_modelo(modelo, t.prompt)
    return (
        t.entrada * p.entrada
        + t.saida * p.saida
        + t.cache_leitura * p.cache_leitura
        + t.cache_5m * p.cache_5m
        + t.cache_1h * p.cache_1h
    ) / 1_000_000


# As iterações que são amostragem de um modelo (com o modelo de cada uma).
# `compaction` não vem no total de cima e é do modelo do pedido.
_ITERACOES_DE_MODELO = ("message", "fallback_message", "advisor_message", "compaction")


def custo_do_uso(usage: Any, modelo: str | None) -> float:
    """Dólar do `usage` de uma resposta. PURA.

    Com `usage.iterations` (laço do servidor, fallback de modelo, conselheiro),
    cada iteração com o preço do SEU modelo (o fallback do braço Sonnet do
    eval responde com outro); sem elas, o total de cima com `modelo`.
    """
    iteracoes = _campo(usage, "iterations")
    if iteracoes:
        partes = [i for i in iteracoes if _campo(i, "type") in _ITERACOES_DE_MODELO]
        if partes:
            return sum(
                custo_dos_tokens(tokens_do_uso(i), _campo(i, "model") or modelo) for i in partes
            )
    return custo_dos_tokens(tokens_do_uso(usage), modelo)


@dataclass
class Uso:
    """O uso somado de uma execução do agente (vai em `fatos.agente.uso`)."""

    requisicoes: int = 0
    entrada: int = 0
    saida: int = 0
    cache_leitura: int = 0
    cache_5m: int = 0
    cache_1h: int = 0
    pensamento: int = 0
    custo_usd: float = 0.0
    modelos: list[str] = field(default_factory=list)

    def somar(self, usage: Any, modelo: str | None) -> float:
        """Soma uma requisição e devolve o custo DELA. Não vai ao Redis."""
        t = tokens_do_uso(usage)
        custo = custo_do_uso(usage, modelo)
        self.requisicoes += 1
        self.entrada += t.entrada
        self.saida += t.saida
        self.cache_leitura += t.cache_leitura
        self.cache_5m += t.cache_5m
        self.cache_1h += t.cache_1h
        self.pensamento += t.pensamento
        self.custo_usd += custo
        if modelo and modelo not in self.modelos:
            self.modelos.append(modelo)
        return custo

    @property
    def tokens_entrada(self) -> int:
        """A coluna `tokens_entrada` do rascunho: tudo o que o modelo leu."""
        return self.entrada + self.cache_leitura + self.cache_5m + self.cache_1h

    def para_fatos(self) -> dict:
        return {
            "requisicoes": self.requisicoes,
            "entrada": self.entrada,
            "saida": self.saida,
            "cache_leitura": self.cache_leitura,
            "cache_5m": self.cache_5m,
            "cache_1h": self.cache_1h,
            "pensamento": self.pensamento,
            "custo_usd": round(self.custo_usd, 6),
            "modelos": list(self.modelos),
        }


def dia_de_hoje(agora: datetime | None = None) -> str:
    """O dia de São Paulo (AAAA-MM-DD) em que o gasto conta."""
    return (agora or datetime.now(SAO_PAULO)).astimezone(SAO_PAULO).strftime("%Y-%m-%d")


def _chaves(
    conversa_id: UUID | str | None, dia: str, *, replay: bool = False
) -> tuple[str, str | None]:
    schema = get_settings().database_schema
    modelo = CHAVE_CONVERSA_REPLAY if replay else CHAVE_CONVERSA
    do_dia = CHAVE_DIA_REPLAY if replay else CHAVE_DIA
    da_conversa = (
        modelo.format(schema=schema, dia=dia, conversa=conversa_id)
        if conversa_id is not None
        else None
    )
    return do_dia.format(schema=schema, dia=dia), da_conversa


def teto_do_dia(*, replay: bool = False) -> float:
    """O teto do dia em dólar: o do agente ao vivo ou o dos testes (<= 0 = sem teto)."""
    s = get_settings()
    if replay:
        return float(s.atendimento_agente_teto_usd_dia_replay or 0)
    return float(s.atendimento_agente_teto_usd_dia or 0)


def _tetos(teto_conversa: float | None = None, *, replay: bool = False) -> tuple[float, float]:
    s = get_settings()
    return (
        teto_do_dia(replay=replay),
        float(
            s.atendimento_agente_teto_usd_conversa or 0 if teto_conversa is None else teto_conversa
        ),
    )


async def _avisar_telegram(texto: str) -> bool:
    """O aviso no Telegram (o teste troca). Nunca levanta."""
    try:
        from app.services.telegram import TelegramClient

        return await TelegramClient().safe_send(texto)
    except Exception as e:  # noqa: BLE001 — o aviso nunca derruba o agente
        logger.warning("atendimento_agente_teto_aviso_falhou", err=type(e).__name__)
        return False


async def avisar_telegram(texto: str) -> bool:
    """O aviso no Telegram, sem limite por dia (a reversão do automático). Nunca levanta."""
    return await _avisar_telegram(texto)


CHAVE_AVISO_DO_DIA = "atd:agente:aviso:{schema}:{tipo}:{dia}"


async def avisar_uma_vez_por_dia(tipo: str, texto: str) -> None:
    """Um aviso no Telegram por `tipo` por dia (SET NX no Redis; revisão 2 de
    20/10/2026: a pausa do agente pelo pedido recusado). Nunca levanta."""
    dia = datetime.now(SAO_PAULO).strftime("%Y-%m-%d")
    chave = CHAVE_AVISO_DO_DIA.format(schema=get_settings().database_schema, tipo=tipo, dia=dia)
    try:
        if not await redis.set(chave, "1", nx=True, ex=TTL_S):
            return
    except Exception as e:  # noqa: BLE001
        logger.warning("atendimento_agente_aviso_sem_redis", err=type(e).__name__)
        return
    if not await _avisar_telegram(texto):
        try:
            await redis.delete(chave)
        except Exception:  # noqa: BLE001, S110
            pass


async def _avisar_teto_do_dia(dia: str, gasto: float, teto: float, *, replay: bool = False) -> None:
    """Uma vez por dia (SET NX no Redis): o agente (ou os testes) parou pelo teto do dia."""
    modelo = CHAVE_AVISO_REPLAY if replay else CHAVE_AVISO
    chave = modelo.format(schema=get_settings().database_schema, dia=dia)
    try:
        if not await redis.set(chave, "1", nx=True, ex=TTL_S):
            return
    except Exception as e:  # noqa: BLE001
        logger.warning("atendimento_agente_teto_indisponivel", err=type(e).__name__)
        return
    if replay:
        texto = (
            f"🤖 <b>Atendimento</b>: os testes do agente (replay, eval, calibração) pararam "
            f"pelo teto deles — US$ {gasto:.2f} de US$ {teto:.2f} em {dia}. O agente ao vivo segue."
        )
    else:
        texto = (
            f"🤖 <b>Atendimento</b>: o agente (Claude) parou pelo teto do dia — "
            f"US$ {gasto:.2f} de US$ {teto:.2f} em {dia}. As conversas seguem para a Caixa Humano."
        )
    if not await _avisar_telegram(texto):
        # Não saiu: devolve a marca para a próxima tentativa avisar.
        try:
            await redis.delete(chave)
        except Exception:  # noqa: BLE001, S110
            pass


async def _conferir(
    dia: str,
    gasto_dia: float,
    gasto_conversa: float | None,
    teto_da_vez: float | None = None,
    *,
    replay: bool = False,
) -> None:
    teto_dia, teto_conversa = _tetos(teto_da_vez, replay=replay)
    if teto_dia > 0 and gasto_dia > teto_dia:
        logger.warning(
            "atendimento_agente_teto_dia", gasto=round(gasto_dia, 4), teto=teto_dia, replay=replay
        )
        await _avisar_teto_do_dia(dia, gasto_dia, teto_dia, replay=replay)
        raise TetoEstourado(TETO_DIA, gasto_dia, teto_dia)
    if teto_conversa > 0 and gasto_conversa is not None and gasto_conversa > teto_conversa:
        logger.warning(
            "atendimento_agente_teto_conversa",
            gasto=round(gasto_conversa, 4),
            teto=teto_conversa,
        )
        raise TetoEstourado(TETO_CONVERSA, gasto_conversa, teto_conversa)


async def registrar(
    usage: Any,
    modelo: str | None,
    *,
    conversa_id: UUID | str | None = None,
    uso: Uso | None = None,
    agora: datetime | None = None,
    replay: bool = False,
    teto_conversa: float | None = None,
    gasto_previo: float = 0.0,
) -> float:
    """Soma o custo desta requisição no dia e na conversa; devolve o custo dela.

    Chamar a CADA requisição (cada item do `async for` do Tool Runner), com o
    `msg.usage` e o modelo que de fato respondeu (`msg.model`). `uso` = o
    acumulado da execução (`ContextoAgente.uso`): soma nele também, e é ele
    que segura o teto da conversa com o Redis fora do ar. Levanta
    `TetoEstourado` DEPOIS de somar (o gasto já aconteceu). `replay` = a
    chave da conversa do replay/eval; `teto_conversa` = o teto da conversa
    desta execução (None = o configurado). `gasto_previo` (ajuste r3) = o que
    OUTRO `uso` da mesma conversa já gastou (o laço, para o verificador): com o
    Redis fora, ele soma no teto da conversa.
    """
    custo = uso.somar(usage, modelo) if uso is not None else custo_do_uso(usage, modelo)
    dia = dia_de_hoje(agora)
    chave_dia, chave_conversa = _chaves(conversa_id, dia, replay=replay)
    gasto_conversa = uso.custo_usd + gasto_previo if uso is not None else None
    try:
        pipe = redis.pipeline(transaction=False)
        pipe.incrbyfloat(chave_dia, custo)
        pipe.expire(chave_dia, TTL_S)
        if chave_conversa is not None:
            pipe.incrbyfloat(chave_conversa, custo)
            pipe.expire(chave_conversa, TTL_S)
        respostas = await pipe.execute()
    except Exception as e:  # noqa: BLE001 — Redis fora: segue (ver o topo)
        logger.warning("atendimento_agente_teto_indisponivel", err=type(e).__name__)
        await _conferir(dia, 0.0, gasto_conversa, teto_conversa, replay=replay)
        return custo
    gasto_dia = float(respostas[0])
    if chave_conversa is not None:
        gasto_conversa = max(float(respostas[2]), gasto_conversa or 0.0)
    await _conferir(dia, gasto_dia, gasto_conversa, teto_conversa, replay=replay)
    return custo


def estimativa_de_requisicao_perdida(modelo: str | None, max_tokens: int) -> float:
    """O custo PROVÁVEL de uma requisição que estourou o tempo (sem `usage`). PURA.

    Pelo lado de cima: a saída inteira (`max_tokens`) e ~10 mil tokens de
    prefixo gravados no cache de 1 h — o teto erra para o lado de parar antes.
    """
    return custo_dos_tokens(
        Tokens(
            entrada=0, saida=max_tokens, cache_leitura=0, cache_5m=0, cache_1h=10_000, pensamento=0
        ),
        modelo,
    )


async def registrar_estimativa(
    custo: float,
    *,
    conversa_id: UUID | str | None = None,
    uso: Uso | None = None,
    agora: datetime | None = None,
    replay: bool = False,
) -> None:
    """Soma no dia e na conversa o custo provável de uma requisição sem `usage`. Nunca levanta."""
    if uso is not None:
        uso.custo_usd += custo
    chave_dia, chave_conversa = _chaves(conversa_id, dia_de_hoje(agora), replay=replay)
    try:
        pipe = redis.pipeline(transaction=False)
        pipe.incrbyfloat(chave_dia, custo)
        pipe.expire(chave_dia, TTL_S)
        if chave_conversa is not None:
            pipe.incrbyfloat(chave_conversa, custo)
            pipe.expire(chave_conversa, TTL_S)
        await pipe.execute()
    except Exception as e:  # noqa: BLE001 — a estimativa nunca derruba o agente
        logger.warning("atendimento_agente_teto_indisponivel", err=type(e).__name__)


async def conferir_teto(
    *,
    conversa_id: UUID | str | None = None,
    uso: Uso | None = None,
    agora: datetime | None = None,
    replay: bool = False,
    teto_conversa: float | None = None,
    gasto_previo: float = 0.0,
) -> None:
    """Levanta `TetoEstourado` se o dia (ou a conversa) já passou do teto. Não soma nada.

    Antes de cada conversa e de cada turno (spec 7.4): o gasto de outra
    rodada do MESMO lado (ao vivo, ou os testes) conta no dia dele. Redis fora: só o
    `uso` (+ o `gasto_previo`). `replay`, `teto_conversa` e `gasto_previo`:
    ver `registrar`.
    """
    dia = dia_de_hoje(agora)
    chave_dia, chave_conversa = _chaves(conversa_id, dia, replay=replay)
    gasto_conversa = uso.custo_usd + gasto_previo if uso is not None else None
    try:
        valores = await redis.mget([chave_dia, *([chave_conversa] if chave_conversa else [])])
    except Exception as e:  # noqa: BLE001
        logger.warning("atendimento_agente_teto_indisponivel", err=type(e).__name__)
        valores = [None, None]
    gasto_dia = _float(valores[0])
    if chave_conversa is not None:
        gasto_conversa = max(_float(valores[1]), gasto_conversa or 0.0)
    # "Passou" aqui é ATINGIU: com o teto já gasto, a próxima requisição passa.
    teto_dia, teto_da_conversa = _tetos(teto_conversa, replay=replay)
    if teto_dia > 0 and gasto_dia >= teto_dia:
        await _avisar_teto_do_dia(dia, gasto_dia, teto_dia, replay=replay)
        raise TetoEstourado(TETO_DIA, gasto_dia, teto_dia)
    if teto_da_conversa > 0 and gasto_conversa is not None and gasto_conversa >= teto_da_conversa:
        raise TetoEstourado(TETO_CONVERSA, gasto_conversa, teto_da_conversa)


def _float(valor: Any) -> float:
    try:
        numero = float(valor or 0)
    except (TypeError, ValueError):
        return 0.0
    return numero if math.isfinite(numero) else 0.0


async def gasto_do_dia(agora: datetime | None = None, *, replay: bool = False) -> float:
    """O gasto do dia em dólar: o do agente ao vivo (a tela e o relatório) ou, com
    `replay`, o dos testes. Redis fora = 0."""
    chave_dia, _ = _chaves(None, dia_de_hoje(agora), replay=replay)
    try:
        return _float(await redis.get(chave_dia))
    except Exception:  # noqa: BLE001
        return 0.0
