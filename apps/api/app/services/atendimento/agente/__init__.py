"""O AGENTE de atendimento (Claude com ferramentas só de leitura, 20/10/2026).

O modelo é o `atendimento_agente_modelo`: o Haiku 5.5 até 23/10/2026 e, desde o item 5
da rodada "redondo" (decisão do dono), o Sonnet 5.5 — o mesmo laço, o mesmo pedido
(raciocínio adaptativo, sem `thinking` nem amostragem; `fallbacks: "default"` só nos
modelos que o aceitam, `_com_fallback`). O VERIFICADOR continua no Haiku 5.5
(`verificador.MODELO`).

Substitui a IA legada (Groq, prompt v10, uma chamada só) canal a canal: roda
quando `agente_config.agente_ligado(settings, canal)` — chave Claude,
interruptor geral e o botão "Agente (Claude)" do canal. Sem isso, o legado
exatamente como está.

O laço é o Tool Runner do SDK `anthropic` 1.9.0
(`client.beta.messages.tool_runner`), dentro do `ia._gerar` (cron
`atendimento_rascunhos` e o "Sugerir" da tela). Quem envia continua sendo o
nosso código, depois das MESMAS travas do legado (`ia.conferir_saida`) e das
do agente (`checagens`, a régua "ação de pessoa" de `acao_pessoa`).

Módulos (contrato completo em `scratchpad/agente/interfaces.md`):

- `contexto`  — `BaseDoGerar` (o que o `_gerar` já leu) e `ContextoAgente`
  (escopo, evidências, garantias, rastro, ações, uso);
- `sessao`    — `sessao_leitura()`: a sessão de cada ferramenta;
- `saida`     — a `Proposta` (resposta final) e a ida à `conferir_saida`;
- `custo`     — preço por modelo, teto em dólar no Redis, aviso no Telegram;
- `pacote`    — a 1ª mensagem (só banco e cache);
- `acao_pessoa` — os gatilhos de código da régua 6.3;
- `checagens` — variações, evidências, promessa da equipe, caminho da
  plataforma, guarda do "perguntar", o outro produto da loja (G1);
- `ferramentas` — as 7 ferramentas (`@beta_async_tool(strict=True)`), a 7ª
  (`buscar_anuncios_da_loja`, 23/10/2026) o catálogo da própria loja;
- `pares`     — a indexação e a busca das respostas da equipe;
- `fatos_loja` — os fatos aprovados por escopo;
- `prompt`    — os 2 blocos de sistema (com cache de 1 h);
- `verificador` — a 2ª camada (20/10/2026): uma requisição sem ferramentas
  que confere o rascunho que PODERIA sair sozinho; sem o "ok" dele, pessoa
  (falha fechada), e a trava do envio exige o registro dele.

`ia.py` só importa este pacote DENTRO de função (o pacote importa `ia`).
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import anthropic
import structlog

from app.config import get_settings
from app.redis_client import redis
from app.services.atendimento import agente_config, constantes, ia
from app.services.atendimento.agente import (
    acao_pessoa,
    checagens,
    contexto,
    custo,
    ferramentas,
    pacote,
    prompt,
    saida,
    verificador,
)
from app.services.atendimento.agente.contexto import BaseDoGerar, ContextoAgente, ExtraAgente
from app.services.atendimento.agente.saida import Proposta
from app.services.atendimento.constantes import (
    PROMPT_VERSAO_AGENTE,
    RASCUNHO_BLOQUEADO,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.atendimento import (
        AtendimentoConversa,
        AtendimentoMensagem,
        AtendimentoRascunho,
    )
    from app.services.atendimento.ia import ErroProvedor, SaidaConferida

__all__ = [
    "PARADAS",
    "BaseDoGerar",
    "ContextoAgente",
    "ExtraAgente",
    "Proposta",
    "ResultadoAgente",
    "responder",
]

# ── Como a execução terminou (`ResultadoAgente.parada`, a coluna
# `atendimento_agente_replay.parada` e `fatos.agente.parada`) ──
PARADA_FIM = "fim"  # o modelo fechou com a Proposta (end_turn + o JSON da Proposta)
PARADA_SEM_AGENTE = "sem_agente"  # gatilho de código antes do modelo: nem chamou
# stop_reason "refusal" (no Sonnet 5.5 o servidor refaz a "cyber"/"frontier_llm" no Sonnet
# 5 com o `fallbacks: "default"`; a que sobra — ou a do Haiku, sem fallback — vem aqui).
PARADA_RECUSA = "recusa"
PARADA_MAX_ITERACOES = "max_iteracoes"  # bateu max_iterations ainda pedindo ferramenta
PARADA_MAX_TOKENS = "max_tokens"  # cortado no max_tokens (já com a nova tentativa)
PARADA_FORMATO = "fora_do_formato"  # o texto final não é a Proposta
PARADA_PRAZO = "prazo"  # passou do prazo cooperativo (45 s)
PARADA_TETO = "teto"  # `custo.TetoEstourado` (qual: `ResultadoAgente.teto_qual`)
PARADA_PROVEDOR = "erro_provedor"  # `ia.ErroProvedor` (rede, 5xx, limite, sem crédito)
# O agente concluiu que a mensagem não pede resposta (ok, obrigado, aviso):
# rascunho DESCARTADO, sem texto e sem pessoa (revisão de 20/10/2026).
PARADA_SEM_RESPOSTA = "sem_resposta"
PARADAS = (
    PARADA_FIM,
    PARADA_SEM_AGENTE,
    PARADA_RECUSA,
    PARADA_MAX_ITERACOES,
    PARADA_MAX_TOKENS,
    PARADA_FORMATO,
    PARADA_PRAZO,
    PARADA_TETO,
    PARADA_PROVEDOR,
    PARADA_SEM_RESPOSTA,
)

# ── Os motivos das paradas que gravam rascunho sem a Proposta (spec 3.3.1) ──
MOTIVO_RECUSA = "recusa do modelo ({categoria})"
MOTIVO_NAO_CONCLUIU = "o agente não concluiu"
MOTIVO_FORA_DO_FORMATO = "a IA respondeu fora do formato combinado"  # = o do legado
MOTIVO_CORTADO = "a resposta do agente foi cortada no limite de tamanho"
MOTIVO_PRAZO = "o agente passou do prazo duas vezes"
MOTIVO_PROVEDOR_DEMOROU = "o agente não concluiu: o provedor não respondeu a tempo duas vezes"
MOTIVO_PROVEDOR_FALHOU = "o agente não concluiu: o provedor falhou três vezes seguidas"
MOTIVO_SEM_RESPOSTA = "o agente concluiu que a mensagem não pede resposta"
# Kia ao vivo (21/10/2026): o turno só de "ok"/"obrigada"/"aguardando" depois
# de a loja já ter tratado o assunto (`acao_pessoa.turno_so_de_reconhecimento`).
MOTIVO_JA_TRATADO = (
    "o cliente só agradeceu ou confirmou depois de a loja tratar o assunto — não pede resposta"
)

# O prazo estourado na 1ª vez não grava rascunho e tenta na próxima rodada; a
# 2ª vai para pessoa (contador por gatilho no Redis, 1 dia). O mesmo para a
# requisição que estourou o tempo (`APITimeoutError`, revisão de 20/10/2026):
# o servidor pode ter cobrado e o `usage` não volta — sem o contador, o mesmo
# gatilho tentaria (e gastaria, fora do teto) a cada rodada.
CHAVE_PRAZO = "atd:agente:prazo:{schema}:{gatilho}"
CHAVE_DEMORA = "atd:agente:demora:{schema}:{gatilho}"
# O erro passageiro do provedor (5xx, 529, rede; revisão 2 de 20/10/2026): na
# 3ª vez do MESMO gatilho, pessoa (o agente inteiro já pausa 1 min a cada vez).
CHAVE_FALHA = "atd:agente:falha:{schema}:{gatilho}"
VEZES_FALHA_ATE_PESSOA = 3


@dataclass
class ResultadoAgente:
    """O que o agente decidiu. O `_gerar` grava o rascunho com isto (`para_rascunho`).

    `gravar=False` = nenhum rascunho nesta rodada (prazo na 1ª vez, teto,
    erro de provedor passageiro, limite, sem crédito): o `_gerar` devolve
    None — ou, com `erro_provedor`, passa pelo `_falha` dele, como o legado.
    Os campos de coluna (`texto`, `categoria`, `confianca`, `precisa_humano`,
    `motivo`, `validador_ok`, `validador_erros`, `status`) são os do
    `AtendimentoRascunho`; `fatos_agente` = `ContextoAgente.para_fatos()`.
    """

    parada: str
    gravar: bool
    texto: str | None = None
    categoria: str | None = None
    confianca: float | None = None
    precisa_humano: bool = True
    motivo: str | None = None
    validador_ok: bool = False
    validador_erros: list[str] = field(default_factory=lambda: ["texto vazio"])
    status: str = RASCUNHO_BLOQUEADO
    acoes_pessoa: list[str] = field(default_factory=list)
    fatos_agente: dict = field(default_factory=dict)
    modelo: str | None = None
    manual_hash: str | None = None
    tokens_entrada: int | None = None
    tokens_saida: int | None = None
    custo_usd: float = 0.0
    proposta: Proposta | None = None
    conferida: SaidaConferida | None = None
    erro_provedor: ErroProvedor | None = None
    # Com `PARADA_TETO`: qual teto (`custo.TETO_DIA` ou `custo.TETO_CONVERSA`).
    # O replay e o eval param só no do dia; no da conversa, pulam o item.
    teto_qual: str | None = None

    @classmethod
    def sem_texto(
        cls, ctx: ContextoAgente, *, parada: str, motivo: str, **extra: Any
    ) -> ResultadoAgente:
        """Rascunho SEM texto, bloqueado, para pessoa (o `ia._novo` do agente)."""
        ctx.parada = parada
        return cls(
            parada=parada,
            gravar=True,
            motivo=motivo[:1000],
            acoes_pessoa=ctx.acoes_pessoa,
            fatos_agente=ctx.para_fatos(),
            modelo=ctx.modelo,
            tokens_entrada=ctx.uso.tokens_entrada if ctx.uso.requisicoes else None,
            tokens_saida=ctx.uso.saida if ctx.uso.requisicoes else None,
            custo_usd=ctx.uso.custo_usd,
            **extra,
        )

    @classmethod
    def sem_rascunho(cls, ctx: ContextoAgente, *, parada: str, **extra: Any) -> ResultadoAgente:
        """Nenhum rascunho nesta rodada (ver `gravar`)."""
        ctx.parada = parada
        return cls(
            parada=parada,
            gravar=False,
            acoes_pessoa=ctx.acoes_pessoa,
            fatos_agente=ctx.para_fatos(),
            modelo=ctx.modelo,
            custo_usd=ctx.uso.custo_usd,
            **extra,
        )

    def para_rascunho(
        self,
        conversa: AtendimentoConversa,
        gatilho: AtendimentoMensagem,
        fatos_gravados: dict,
    ) -> AtendimentoRascunho:
        """O `AtendimentoRascunho` (sem gravar): `prompt_versao="ag1"` e `fatos.agente`."""
        from app.models.atendimento import AtendimentoRascunho

        if not self.gravar:
            raise ValueError("resultado do agente sem rascunho")
        return AtendimentoRascunho(
            conversa_id=conversa.id,
            mensagem_gatilho_id=gatilho.id,
            texto=self.texto or None,
            categoria=self.categoria,
            confianca=self.confianca,
            precisa_humano=self.precisa_humano,
            motivo=self.motivo,
            validador_ok=self.validador_ok,
            validador_erros=list(self.validador_erros),
            fatos={**fatos_gravados, "agente": self.fatos_agente},
            modelo=str(self.modelo)[:128] if self.modelo else None,
            prompt_versao=PROMPT_VERSAO_AGENTE,
            manual_hash=self.manual_hash,
            tokens_entrada=self.tokens_entrada,
            tokens_saida=self.tokens_saida,
            status=self.status,
        )


# ── O laço (spec 3.3, 3.3.1, 3.4 e 7; dono: "cérebro") ────────────────────

logger = structlog.get_logger()

# O pedido ao modelo (spec 3.3 e 7.1). Haiku 5.5 e Sonnet 5.5: sem `temperature`,
# `top_p`, `top_k` nem `thinking` (fora do padrão dá 400; o raciocínio é
# adaptativo — e o Sonnet 5.5 recusa `thinking` {"type": "disabled"}); o esforço vai em
# `output_config.effort`. O raciocínio conta no `max_tokens` (item 5 da rodada
# "redondo", 23/10/2026: o eval S1 do Sonnet, em medium, coube nos 4.000 por requisição).
MAX_TOKENS = 4_000
# Revisão de 20/10/2026: o pedido não é streaming — o tempo de leitura cobre a
# geração inteira (o raciocínio incluso). 25 s cortava resposta longa, e a
# nova tentativa do SDK repetia o mesmo pedido (até 2×25 s numa rodada de 300
# s). Agora 45 s (o mesmo da Claude no legado, `ia._TIMEOUT_CLAUDE`) e sem
# nova tentativa no SDK, como o legado: o limite e o 5xx voltam à rodada do
# cron (1 min) e o tempo estourado tem o contador por gatilho
# (`CHAVE_DEMORA`: a 2ª vez vai para pessoa).
# Ajuste r3: 8.000 → 6.000 — sem streaming, a 2ª passada inteira cabe nos
# 45 s de leitura (~150–200 tokens/s, o raciocínio incluso); 8.000 estourava o
# tempo e a 1ª passada ficava paga à toa.
MAX_TOKENS_NOVA_PASSADA = 6_000
TIMEOUT = anthropic.Timeout(45.0, connect=8.0)
MAX_RETRIES = 0
# Os esforços que a API aceita; outro valor no `.env` (erro de digitação)
# daria 400 em todo pedido: vale o padrão, com aviso no log.
EFFORTS = ("low", "medium", "high", "xhigh", "max")
EFFORT_PADRAO = "medium"
# A cauda do laço: o ponto de cache automático do pedido (5 min). Os 2 do
# sistema (1 h) + este = 3 dos 4 que a API aceita.
CACHE_CAUDA = {"type": "ephemeral"}
# O fallback do servidor (spec 7.1): o braço Sonnet do eval e, desde o item 5 da rodada
# "redondo" (23/10/2026), o agente ao vivo no Sonnet 5.5; o Haiku 5.5 não aceita
# `fallbacks`. A resposta refeita vem com o id do outro modelo (`msg.model`), que o
# `custo` conhece; a recusa que sobra para no `PARADA_RECUSA` (pessoa, sem texto).
_MODELOS_COM_FALLBACK = ("claude-sonnet-", "claude-opus-", "claude-fable-")
# As paradas de um turno que cortaram a resposta e as que ainda pedem outra
# volta (ferramenta ou continuação do servidor); as outras fecham o laço.
_CORTADA = ("max_tokens", "model_context_window_exceeded")
_CONTINUA = ("tool_use", "pause_turn", "compaction")
# `max_tokens` (sem texto, ou com o JSON cortado): 1 nova tentativa (spec
# 3.3.1, item 4) — 2 passadas no máximo.
PASSADAS_MAX_TOKENS = 2
# O contador do prazo estourado vive 1 dia (`CHAVE_PRAZO`).
TTL_PRAZO_S = 24 * 3600
MOTIVO_SEM_CHAVE = "sem chave da Claude para o agente (ATENDIMENTO_AGENTE_API_KEY com sk-ant-)"
MOTIVO_ANTES_DO_MODELO = "ação de pessoa antes do agente"

# O relógio do prazo cooperativo (o teste troca; nada de `asyncio.wait_for`
# cancelando o laço no meio de uma ferramenta — a renovação de token da loja
# é de uso único).
_relogio = time.monotonic


def _com_fallback(modelo: str) -> bool:
    return modelo.startswith(_MODELOS_COM_FALLBACK)


# O orçamento do VERIFICADOR (ajuste r3 de 20/10/2026): antes ele ficava só com
# a sobra do prazo cooperativo — a resposta final que chegava perto (ou depois)
# do prazo ia sempre para pessoa ("sem tempo"). Na rodada do cron ele tem pelo
# menos `verificador.TIMEOUT_S`, limitado ao que sobra da rodada
# (`ia.TEMPO_DA_RODADA_S`) menos `MARGEM_DA_RODADA_S` (o envio, a reavaliação
# e o reenvio). O pior caso de uma conversa do agente na rodada fica em ~233 s
# de 300 (ver `ia.AGENTE_FIM_DO_LACO_S`). No "Sugerir" ele nem roda.
MARGEM_DA_RODADA_S = 60.0


def _prazo_do_verificador(prazo: float, *, forcar: bool) -> float:
    """Os segundos do verificador nesta conversa (ver acima)."""
    resta = prazo - _relogio()
    if forcar:
        return resta
    orcamento = max(resta, verificador.TIMEOUT_S)
    inicio = ia._INICIO_DA_RODADA.get()
    if inicio is not None:
        da_rodada = ia.TEMPO_DA_RODADA_S - (time.monotonic() - inicio) - MARGEM_DA_RODADA_S
        orcamento = min(orcamento, da_rodada)
    return orcamento


def _em_ordem(tools: Sequence[Any]) -> list[Any]:
    """As ferramentas na ordem de `ferramentas.NOMES` (o cache do prefixo depende dela)."""
    ordem = {nome: i for i, nome in enumerate(ferramentas.NOMES)}
    return sorted(tools, key=lambda t: ordem.get(getattr(t, "name", None), len(ordem)))


def argumentos_do_laco(
    *,
    modelo: str,
    effort: str,
    max_turnos: int,
    sistema: list[dict],
    tools: Sequence[Any],
    texto_do_pacote: str,
) -> dict[str, Any]:
    """Os argumentos de `cliente.beta.messages.tool_runner` (spec 3.3). PURA.

    O formato da resposta vai em `output_config.format` com o esquema da
    `Proposta` (`saida.esquema()`, o mesmo que o SDK geraria) e NÃO em
    `output_format=Proposta`: no `anthropic` 1.9.0 o `output_format` está
    "deprecated" (o próprio SDK manda usar `output_config.format`) e o
    `beta.messages.parse` faria `validate_json` em TODO bloco de texto de
    toda resposta — uma frase antes de chamar a ferramenta ou o JSON cortado
    no `max_tokens` levantariam `ValidationError` de dentro do laço, sem a
    mensagem. O corpo que vai à API é o mesmo; quem lê a `Proposta` é
    `proposta_da_mensagem`, só na resposta final.
    """
    argumentos: dict[str, Any] = {
        "model": modelo,
        "max_tokens": MAX_TOKENS,
        "max_iterations": max_turnos,
        "system": sistema,
        "tools": _em_ordem(tools),
        "messages": [{"role": "user", "content": texto_do_pacote}],
        "output_config": {
            "effort": effort,
            "format": {"type": "json_schema", "schema": saida.esquema()},
        },
        "cache_control": dict(CACHE_CAUDA),
    }
    if _com_fallback(modelo):
        argumentos["betas"] = [ia._BETA_FALLBACK]
        argumentos["fallbacks"] = "default"
    return argumentos


def proposta_da_mensagem(mensagem: Any) -> Proposta | None:
    """A `Proposta` do texto da resposta final, ou None (fora do formato).

    O último bloco de texto que é a `Proposta` inteira; sem nenhum, os blocos
    juntos (o JSON partido em dois blocos).
    """
    textos = [
        str(b.text)
        for b in getattr(mensagem, "content", None) or []
        if getattr(b, "type", None) == "text" and getattr(b, "text", None)
    ]
    for texto in reversed(textos):
        proposta = saida.ler_proposta(texto)
        if proposta is not None:
            return proposta
    return saida.ler_proposta("".join(textos)) if len(textos) > 1 else None


# O limite MENSAL do workspace no Console da Anthropic (o freio de fora da
# spec 7.4) volta como 400 "You have reached your specified workspace API
# usage limits": para o agente é falta de crédito (pausa), não um bloqueado
# por conversa (revisão de 20/10/2026; o `ia._FRASES_SEM_CREDITO` do legado
# fica como está).
_LIMITE_DO_WORKSPACE = ("usage limits", "usage_limit")
# O TIPO do erro do provedor no agente (`ErroProvedor.codigo`; revisão 2 de
# 20/10/2026) — o `_gerar_pelo_agente` decide por ele, nunca pelo `_falha` do
# legado (que não pausa e deixava a conversa voltar a cada minuto):
#   - configuração (401/403/404: a chave revogada, a conta, o modelo que a
#     conta não tem) → a pausa longa do agente inteiro;
#   - passageiro (5xx, 529, rede) → a pausa curta do agente inteiro, e o
#     contador por gatilho (`CHAVE_FALHA`: na 3ª vez, pessoa);
#   - o pedido inválido (400/422 que fala de parâmetro ou formato — não do
#     texto da conversa: `output_config`, `cache_control`, o esquema
#     `strict`...) → a pausa longa do agente, com aviso no Telegram;
#   - o pedido recusado (outro 400/422) → o rascunho bloqueado, como o
#     legado — e, repetido em outra conversa seguida, a pausa longa.
ERRO_CONFIGURACAO = "agente_configuracao"
ERRO_PASSAGEIRO = "agente_passageiro"
ERRO_PEDIDO_INVALIDO = "agente_pedido_invalido"
ERRO_PEDIDO_RECUSADO = "agente_pedido_recusado"
_HTTP_CONFIGURACAO = (401, 403, 404)
_FALA_DE_PARAMETRO = (
    "output_config",
    "output_format",
    "cache_control",
    "input_schema",
    "json_schema",
    "strict",
    "effort",
    "max_tokens",
    "max_iterations",
    "temperature",
    "top_p",
    "top_k",
    "thinking",
    "fallbacks",
    "betas",
    "anthropic-beta",
    "tools.",
    "tool_choice",
    "model:",
    "extra inputs",
    "not permitted",
    "unexpected value",
    "does not support",
    "is not supported",
    "unsupported",
)


def _do_formato(corpo: Any) -> bool:
    """O 400/422 fala de parâmetro/formato do PEDIDO (não do texto da conversa)?"""
    texto = str(corpo or "").lower()
    return any(trecho in texto for trecho in _FALA_DE_PARAMETRO)


def erro_do_provedor(e: anthropic.AnthropicError, modelo: str) -> ia.ErroProvedor:
    """O erro do SDK no `ErroProvedor` do legado — a MESMA semântica do `ia._chamar_claude`.

    Rede/timeout → "falha de rede (...)"; sem crédito (ou o limite do
    workspace) → `ia._sem_credito` (definitivo para a conta); 413/429 →
    `limite` com o Retry-After; 400/422 → definitivo; o resto
    (401/403/404/5xx/529) é passageiro. A chave nunca entra no `Provedor`
    daqui (só o nome do provedor importa). O `codigo` diz o tipo para o
    agente (`ERRO_*`, revisão 2 de 20/10/2026). Ajuste r3: o tempo esgotado
    também é passageiro (pausa o agente 1 min, como o 5xx): num incidente de
    latência, sem a pausa, cada rodada começava conversas novas presas 45 s.
    O contador por gatilho dele (`CHAVE_DEMORA`) continua só no `responder`.
    """
    if isinstance(e, anthropic.APITimeoutError):
        return ia.ErroProvedor(f"falha de rede ({type(e).__name__})", codigo=ERRO_PASSAGEIRO)
    if isinstance(e, anthropic.APIConnectionError):
        return ia.ErroProvedor(f"falha de rede ({type(e).__name__})", codigo=ERRO_PASSAGEIRO)
    if isinstance(e, anthropic.APIStatusError):
        codigo = ia.falta_de_credito(e.status_code, e.body)
        if not codigo and e.status_code in (400, 403, 429):
            corpo = str(e.body or "").lower()
            if any(frase in corpo for frase in _LIMITE_DO_WORKSPACE):
                codigo = "workspace_usage_limit"
        if codigo:
            p = ia.Provedor(ia.BASE_CLAUDE, modelo, "", ia.PROVEDOR_CLAUDE)
            return ia._sem_credito(p, e.status_code, codigo)
        if e.status_code in ia._HTTP_LIMITE:
            return ia.ErroProvedor(
                f"provedor devolveu {e.status_code}",
                limite=True,
                espera=ia._espera_pedida(e.response.headers.get("retry-after")),
            )
        definitivo = e.status_code in ia._HTTP_DEFINITIVO
        if definitivo:
            tipo = ERRO_PEDIDO_INVALIDO if _do_formato(e.body) else ERRO_PEDIDO_RECUSADO
        elif e.status_code in _HTTP_CONFIGURACAO:
            tipo = ERRO_CONFIGURACAO
        else:
            tipo = ERRO_PASSAGEIRO
        return ia.ErroProvedor(
            f"provedor devolveu {e.status_code}", definitivo=definitivo, codigo=tipo
        )
    # Resposta fora do esquema do SDK e afins: passageiro.
    return ia.ErroProvedor(
        f"falha no cliente da Claude ({type(e).__name__})", codigo=ERRO_PASSAGEIRO
    )


# Rodada "fechar o eval" (23/10/2026): no eval real do Sonnet 5.5 (27 turnos da Shopee), 4
# terminaram no último pedido permitido ainda pedindo ferramenta — sem texto, pessoa. O
# pedido que será o último (`max_turnos`) ou que começa com menos de
# `FECHAR_ANTES_DO_PRAZO_S` do prazo vai com `tool_choice` {"type": "none"}: o modelo
# responde com o que já leu (a Proposta), sem ferramenta. O "none" vale no Haiku 5.5 e no
# Sonnet 5.5 (só o "any"/"tool" forçados dão 400 no Sonnet 5.5) e muda só esse pedido.
TOOL_CHOICE_SEM_FERRAMENTAS = {"type": "none"}
FECHAR_ANTES_DO_PRAZO_S = 15.0


def fechar_na_proxima(turnos: int, max_turnos: int, prazo: float) -> bool:
    """O próximo pedido deve ir sem ferramentas (ver acima)?"""
    return turnos + 1 >= max_turnos or prazo - _relogio() < FECHAR_ANTES_DO_PRAZO_S


def _sem_ferramentas(params: Any) -> Any:
    """Os parâmetros do runner com o `tool_choice` que fecha o laço (para `set_messages_params`)."""
    return {**params, "tool_choice": dict(TOOL_CHOICE_SEM_FERRAMENTAS)}


@dataclass
class _Volta:
    """Uma passada do Tool Runner: a última mensagem e por que parou."""

    ultima: Any = None
    turnos: int = 0
    estourou_prazo: bool = False


async def _passada(
    cliente: Any,
    argumentos: dict[str, Any],
    ctx: ContextoAgente,
    *,
    conversa_id: Any,
    prazo: float,
    max_turnos: int,
) -> _Volta:
    """Uma passada do runner. Levanta `custo.TetoEstourado` e os erros do SDK.

    Cada mensagem: custo no Redis (`custo.registrar`, levanta o teto DEPOIS
    de somar) e o turno no `ctx`. A resposta FINAL que passou do teto não se
    perde (já foi paga; revisão de 20/10/2026): o teto vale para a próxima
    execução. O prazo é conferido ENTRE um turno e outro (cooperativo):
    passou, o laço sai antes de rodar as ferramentas do turno. No último
    turno permitido ainda pedindo ferramenta, sai também (as ferramentas
    dele nem rodam: o runner pararia logo depois delas). Rodada "fechar o
    eval" (23/10/2026): o pedido que será o último (ou que começa perto do
    prazo) já vai sem ferramentas (`fechar_na_proxima`) — a saída sem texto
    só sobra quando o modelo nem assim responde.

    Toda saída (o fim, o corte, o prazo, o teto, o erro do SDK) fecha os
    geradores do runner ANTES de voltar (`fechar_runner`, 21/10/2026).
    """
    volta = _Volta()
    runner = cliente.beta.messages.tool_runner(**argumentos)
    iterador = runner.__aiter__()
    try:
        async for mensagem in iterador:
            volta.ultima = mensagem
            volta.turnos += 1
            ctx.turnos += 1
            ctx.modelo = getattr(mensagem, "model", None) or ctx.modelo
            final = mensagem.stop_reason not in _CONTINUA
            try:
                await custo.registrar(
                    mensagem.usage,
                    ctx.modelo,
                    conversa_id=conversa_id,
                    uso=ctx.uso,
                    replay=ctx.modo_replay,
                    teto_conversa=ctx.teto_conversa_usd,
                )
            except custo.TetoEstourado as e:
                if not final:
                    raise
                logger.info("atendimento_agente_teto_na_resposta_final", qual=e.qual)
            if final:
                break
            if mensagem.stop_reason == "tool_use" and volta.turnos >= max_turnos:
                break
            if _relogio() > prazo:
                volta.estourou_prazo = True
                break
            if (
                mensagem.stop_reason == "tool_use"
                and not ctx.fechou_sem_ferramentas
                and fechar_na_proxima(volta.turnos, max_turnos, prazo)
            ):
                # As ferramentas deste turno ainda rodam; o próximo pedido é a resposta.
                runner.set_messages_params(_sem_ferramentas)
                ctx.fechou_sem_ferramentas = True
    finally:
        await fechar_runner(runner, iterador)
    return volta


async def fechar_runner(runner: Any, iterador: Any) -> None:
    """Fecha AGORA os geradores do Tool Runner, na ordem certa (21/10/2026). Nunca levanta.

    O eval real de 21/10 logou, no fim de TODA conversa, "RuntimeError:
    generator didn't stop after athrow()" no `BaseAsyncToolRunner.__run__`
    (`anthropic` 1.9.0, `lib/tools/_beta_runner.py`). O `break` do laço
    deixava suspensos o `__aiter__` (o gerador do `async for`) e o
    `__run__` (`runner._iterator`) — este dentro do `async with
    self._handle_request(...)`, cujo `asynccontextmanager` é outro gerador.
    O runner e o `__run__` fazem ciclo (o `self` no quadro): só o GC ou o
    `loop.shutdown_asyncgens()` os fechavam, em ordem QUALQUER. Fechado o
    `_handle_request` primeiro, o `__aexit__` do `__run__` faz `athrow` num
    gerador já terminado, o Python 3.12 (o da imagem) devolve sem levantar e
    o `contextlib` acusa o erro.

    Aqui: o de fora (`aclose` no `yield` do `async for`) e depois o
    `__run__` — o `GeneratorExit` desce pelo `async with`, e o `__aexit__`
    fecha o `_handle_request` ainda vivo; nada sobra para o fim do loop. O
    comportamento não muda: fechar no `yield` do turno não faz requisição
    nem roda ferramenta (as ferramentas só rodam depois que o `async with`
    sai normalmente). Gerador já terminado (o erro do SDK, o fim natural):
    o `aclose` não faz nada.
    """
    for gerador in (iterador, getattr(runner, "_iterator", None)):
        fechar = getattr(gerador, "aclose", None)
        if fechar is None:
            continue
        try:
            await fechar()
        except Exception as e:  # noqa: BLE001 — fechar nunca derruba a resposta
            logger.warning("atendimento_agente_runner_nao_fechou", err=type(e).__name__)


async def _laco(
    cliente: Any,
    argumentos: dict[str, Any],
    ctx: ContextoAgente,
    *,
    conversa_id: Any,
    prazo: float,
    max_turnos: int,
) -> _Volta:
    """As passadas: a 2ª só quando a 1ª foi cortada no `max_tokens` e ainda há prazo.

    A 2ª (revisão de 20/10/2026) não repete o MESMO pedido (o corte tende a se
    repetir, pelo dobro): vai com `MAX_TOKENS_NOVA_PASSADA`, e o `ctx` volta
    ao estado de antes da 1ª (o orçamento das ferramentas, o que elas
    provaram e as garantias — o modelo da 2ª não viu nada disso).
    """
    volta = _Volta()
    antes = ctx.estado_das_ferramentas()
    ctx.consultas, ctx.fechou_sem_ferramentas = [], False
    for passada in range(1, PASSADAS_MAX_TOKENS + 1):
        ctx.max_tokens_da_passada = int(argumentos.get("max_tokens") or MAX_TOKENS)
        volta = await _passada(
            cliente, argumentos, ctx, conversa_id=conversa_id, prazo=prazo, max_turnos=max_turnos
        )
        cortada = getattr(volta.ultima, "stop_reason", None) == "max_tokens"
        if not cortada or passada == PASSADAS_MAX_TOKENS or _relogio() > prazo:
            break
        logger.info("atendimento_agente_nova_tentativa", motivo="max_tokens", turnos=ctx.turnos)
        ctx.voltar_ferramentas(antes)
        argumentos = {**argumentos, "max_tokens": MAX_TOKENS_NOVA_PASSADA}
    return volta


async def _soltar_a_sessao(session: AsyncSession, ctx: ContextoAgente) -> None:
    """Commit na sessão do `_gerar` ANTES do laço (spec 3.3.1, regra 8).

    O laço dura até o prazo (45 s) e o Postgres de produção não tem
    `idle_in_transaction_session_timeout`: a sessão não fica "idle in
    transaction" segurando snapshot e trava. O `sessionmaker` tem
    `expire_on_commit=False` (a conversa e o gatilho continuam legíveis).
    No replay o laço não grava nada: com objeto pendente na sessão (de quem
    chamou), não é o agente que vai gravá-lo — fica sem o commit.
    """
    pendente = bool(
        getattr(session, "new", None)
        or getattr(session, "dirty", None)
        or getattr(session, "deleted", None)
    )
    if ctx.modo_replay and pendente:
        logger.warning("atendimento_agente_replay_sessao_com_escrita")
        return
    await session.commit()


async def _vezes_do_prazo(gatilho_id: Any, *, chave_modelo: str = CHAVE_PRAZO) -> int:
    """Quantas vezes ESTE gatilho passou do prazo (contador no Redis, 1 dia).

    Redis fora: conta como a 2ª (vai para pessoa) — sem o contador, o mesmo
    gatilho tentaria (e gastaria) a cada rodada para sempre. `chave_modelo` =
    `CHAVE_DEMORA` para o tempo estourado da requisição.
    """
    chave = chave_modelo.format(schema=get_settings().database_schema, gatilho=gatilho_id)
    try:
        vezes = int(await redis.incr(chave))
        await redis.expire(chave, TTL_PRAZO_S)
    except Exception as e:  # noqa: BLE001 — o contador nunca derruba o agente
        logger.warning("atendimento_agente_prazo_sem_redis", err=type(e).__name__)
        return 2
    return vezes


# O cache ENTRE conversas (spec 7.2; revisão 2 de 20/10/2026): o prefixo do
# sistema (1 h) que uma conversa grava a seguinte lê. Execuções seguidas SEM
# nenhuma leitura de cache (neste processo) = o prefixo mudou a cada uma —
# aviso no log a cada `CACHE_ZERADO_SEGUIDAS`.
CACHE_ZERADO_SEGUIDAS = 5
_sem_cache_seguidas = [0]


def _contar_cache_entre_conversas(uso: Any, sistema_hash: str) -> None:
    if not uso.requisicoes:
        return
    if uso.cache_leitura:
        _sem_cache_seguidas[0] = 0
        return
    _sem_cache_seguidas[0] += 1
    if _sem_cache_seguidas[0] % CACHE_ZERADO_SEGUIDAS == 0:
        logger.warning(
            "atendimento_agente_cache_zerado_entre_conversas",
            seguidas=_sem_cache_seguidas[0],
            sistema=sistema_hash,
        )


@dataclass
class _Execucao:
    """O que fecha qualquer saída do `responder`: ms, hash do sistema, log."""

    ctx: ContextoAgente
    t0: float
    sistema_hash: str
    manual_hash: str | None

    def _ms(self) -> None:
        self.ctx.ms = max(0, int((_relogio() - self.t0) * 1000))

    def pronto(self, r: ResultadoAgente, **fatos_extra: Any) -> ResultadoAgente:
        r.fatos_agente = {**r.fatos_agente, "sistema": self.sistema_hash, **fatos_extra}
        u = self.ctx.uso
        # Só números e códigos: nunca texto do cliente, nunca a chave.
        logger.info(
            "atendimento_agente",
            parada=r.parada,
            gravar=r.gravar,
            status=r.status if r.gravar else None,
            acoes=r.acoes_pessoa,
            turnos=self.ctx.turnos,
            ferramentas=len(self.ctx.rastro),
            ms=self.ctx.ms,
            requisicoes=u.requisicoes,
            entrada=u.entrada,
            cache_lido=u.cache_leitura,
            cache_gravado=u.cache_5m + u.cache_1h,
            saida=u.saida,
            custo_usd=round(u.custo_usd, 6),
            modelo=self.ctx.modelo,
        )
        if u.requisicoes >= 2 and not u.cache_leitura:
            # A 2ª requisição da MESMA execução repete o prefixo da 1ª: sem
            # leitura do cache, algo variável entrou no sistema ou nas
            # ferramentas (spec 7.2).
            logger.warning(
                "atendimento_agente_cache_zerado",
                sistema=self.sistema_hash,
                requisicoes=u.requisicoes,
            )
        _contar_cache_entre_conversas(u, self.sistema_hash)
        return r

    def sem_texto(self, parada: str, motivo: str, **extra: Any) -> ResultadoAgente:
        self._ms()
        extra.setdefault("manual_hash", self.manual_hash)
        return self.pronto(
            ResultadoAgente.sem_texto(self.ctx, parada=parada, motivo=motivo, **extra)
        )

    def sem_rascunho(self, parada: str, **extra: Any) -> ResultadoAgente:
        self._ms()
        extra.setdefault("manual_hash", self.manual_hash)
        return self.pronto(ResultadoAgente.sem_rascunho(self.ctx, parada=parada, **extra))


# O que a `conferir_saida` diz de um texto vazio (o validador): o único
# motivo que o "não pede resposta" aceita.
_SO_TEXTO_VAZIO = "validador: texto vazio"


def _guardar_avisos(ctx: ContextoAgente, final: SaidaConferida) -> None:
    """O que o rascunho grava das regras (rodada 4, 21/10/2026): os avisos do
    copiloto de antes (`avisos_auto`), os avisos das regras suaves que a pessoa
    vê (`avisos`) e por que não sai sozinho (`sem_auto`, que a trava lê)."""
    ctx.avisos_auto = list(final.avisos_auto)
    ctx.avisos = list(final.avisos)
    ctx.sem_auto = list(final.sem_auto)


def _nao_pede_resposta(
    p: Proposta, conferida: SaidaConferida, ctx: ContextoAgente, base: BaseDoGerar
) -> bool:
    """O agente respondeu "responder" sem texto, sem ação de pessoa, sem pergunta
    pendente no turno e sem nada que segure (fora o "texto vazio")? Não no
    "Sugerir" (a pessoa está na tela e vê o motivo)."""
    return (
        p.acao == saida.RESPONDER
        and not (conferida.texto or "").strip()
        and not ctx.acoes_pessoa
        and not base.foco
        and not base.forcar
        and all(m == _SO_TEXTO_VAZIO for m in conferida.motivos)
    )


def _ja_tratado_pela_loja(
    p: Proposta,
    conferida: SaidaConferida,
    ctx: ContextoAgente,
    base: BaseDoGerar,
    gatilho: Any | None,
) -> bool:
    """O cliente só agradeceu/confirmou ("Ok Aguardando", "Obrigada") depois de a
    loja tratar o assunto, e nada do CÓDIGO segura? (Kia ao vivo, 21/10/2026,
    rascunho 15da122e: o agente repetiu a orientação e pediu `abrir_chamado`
    que a equipe já tinha prometido.) Então não há o que responder, seja o que
    o modelo propôs — menos a linha curta de cortesia dele ("Por nada!
    Qualquer coisa, estamos à disposição."), que sai como sempre. Não no
    "Sugerir" (a pessoa pediu a sugestão)."""
    if base.forcar or ctx.acoes_codigo:
        return False
    if not acao_pessoa.turno_so_de_reconhecimento(base.recentes, gatilho):
        return False
    cortesia = checagens.so_cortesia_de_fechamento(conferida.texto)
    return not (p.acao == saida.RESPONDER and not ctx.acoes_modelo and cortesia)


@dataclass
class Conferido:
    """O que a 1ª camada (determinística) decidiu sobre a Proposta do modelo — antes do
    verificador (`conferir_proposta`). `sem_resposta` = nada a responder (o rascunho
    DESCARTADO); `ja_tratado` = o turno só de reconhecimento depois de a loja tratar."""

    proposta: Proposta
    conferida: SaidaConferida
    chk: checagens.Checagens
    final: SaidaConferida | None = None
    codigos: list[str] = field(default_factory=list)
    sem_resposta: bool = False
    ja_tratado: bool = False


async def conferir_proposta(
    session: AsyncSession | None,
    conversa: AtendimentoConversa,
    gatilho: AtendimentoMensagem,
    base: BaseDoGerar,
    ctx: ContextoAgente,
    p: Proposta,
    pac: Any,
    *,
    forcar_conferencia: bool | None = None,
) -> Conferido:
    """A 1ª camada sobre a Proposta do modelo: a `conferir_saida(agente=True)`, as checagens,
    o andamento, o PORTÃO (27/10/2026: o que é duro segura; o resto vira aviso) e o
    "não pede resposta". Sem modelo e sem verificador — o `responder` e a re-análise
    offline (`scripts.agente_eval.regatilho`) passam pelo MESMO código. `pac` = o
    `pacote.Pacote` (`fatos`, `fichas`, `pedido`); `forcar_conferencia` = o `forcar` da
    `conferir_saida` (None = `base.forcar` ou o replay)."""
    forcar = (base.forcar or ctx.modo_replay) if forcar_conferencia is None else forcar_conferencia
    # Rodada "fechar o eval" (23/10/2026): a política da entrega dita com a resposta-padrão
    # da MESMA loja no pacote ganha o par:<id> nas evidências; e, no "responde e marca"
    # (D1), a `reclamacao_aberta` que o modelo ainda pediu vira a ação de depois do envio.
    p = checagens.com_par_da_politica(p, ctx)
    p, pos_envio_do_modelo = checagens.responde_e_marca_da_proposta(p, ctx)

    # Os OUTROS pedidos que as ferramentas leram: a régua de ESTADO roda neles
    # também (revisão de 20/10/2026) — o código sempre vence.
    andamento_de_outro: list[checagens.PendenciaAgente] = []
    for ctx_pedido, postado in ctx.pedidos_lidos:
        dele = acao_pessoa.gatilhos_de_outro_pedido(ctx_pedido, postado, base.recentes, gatilho)
        ctx.acoes_codigo = contexto.ordenar_acoes(ctx.acoes_codigo, [g.codigo for g in dele])
        andamento_de_outro += checagens.conferir_andamento(ctx_pedido)

    # Rodada 4b: o assunto que o rascunho trata pelas lacunas também precisa
    # estar liberado ("…a previsão é {previsao_entrega}" declarado nota_fiscal).
    ctx.pistas_turno = list(
        dict.fromkeys([*ctx.pistas_turno, *acao_pessoa.pistas_do_rascunho(p.texto)])
    )
    # A proposta passa pelas MESMAS travas do legado e depois pelas do agente.
    ctx.acao = p.acao
    ctx.acoes_modelo = [*saida.acoes_do_modelo(p), *pos_envio_do_modelo]
    ctx.evidencias = saida.evidencias(p)
    # A pergunta PÚBLICA: só a regra da linha (sem data, sem pedido) entra na
    # conferência da garantia; sem ela, None (antes da compra).
    garantias = ctx.garantias_conferidas
    if conversa.canal == constantes.CANAL_PERGUNTA:
        garantias = [g for g in garantias if g.get("regra") and not g.get("status")] or None
    entrada = saida.para_conferir(p, base.ids_categorias)
    # Integração r5 (21/10/2026): a lacuna crua no motivo_pessoa ("Pedido
    # {numero_pedido} consta…") vira o valor, ou sai — ninguém a preenche.
    entrada["motivo_modelo"] = checagens.motivo_sem_lacuna(entrada["motivo_modelo"], base.valores)
    # Barbosa ao vivo (21/10/2026, cd4ff3d8): "em os pedidos no app do TikTok Shop"
    # vira "nos pedidos do app do TikTok Shop" antes da conferência (o texto que vai).
    entrada["texto"] = prompt.caminho_na_frase(entrada["texto"])
    conferida = await ia.conferir_saida(
        session,
        conversa,
        gatilho,
        **entrada,
        motivos=base.motivos,
        ctx=base.ctx,
        fatos=pac.fatos,
        valores=base.valores,
        categorias=base.categorias,
        regras=base.regras,
        textos_cliente=base.textos_cliente,
        textos_rajada=base.textos_rajada,
        foco=base.foco,
        # As fichas do pacote e as que o `ver_anuncio` leu: a trava da ficha
        # no automático vale para as duas (revisão de 20/10/2026).
        fichas=[*pac.fichas, *ctx.fichas_lidas],
        resposta_aprovada=False,
        garantias_conferidas=garantias,
        garantia_consultada=ctx.garantia_consultada,
        permitidos_extra=ctx.permitidos_extra(),
        modo=base.modo,
        nf_politica_aprovada=base.nf_politica_aprovada,
        opcoes_sensiveis=base.opcoes_sensiveis,
        forcar=forcar,
        agente=True,
        # Item 32 (22/10/2026, 3c): a marca da devolução de rotina do turno. D1 (23/10/2026):
        # e o "responde e marca" (a devolução aberta segue com a pessoa DEPOIS do envio).
        devolucao_de_rotina=ctx.devolucao_de_rotina or ctx.responde_e_marca,
        # Decisão do dono (22/10/2026): a pergunta de produto com a resposta da equipe
        # do mesmo anúncio, de um modelo, citada.
        produto_pela_equipe=bool(ctx.pares_citados(ctx.evidencias, de_produto=True)),
        # Item 2 da auditoria ao vivo (22/10/2026, 3ee1f81e): os prazos dos fatos
        # aprovados citados ("1 ano de garantia: os 90 primeiros dias com a loja…").
        fatos_citados=ctx.fatos_citados(ctx.evidencias),
        # Liberações do dono (23/10/2026, L1 e L2): a resposta de produto ou de condição
        # que se apoia só no anúncio da conversa (a `checagens.conferir` confere de novo
        # no texto final e marca `ctx.produto_pelo_anuncio`).
        produto_pelo_anuncio=checagens.liberacao_da_proposta(
            p,
            entrada["texto"],
            entrada["categoria"],
            ctx,
            base,
            fichas=[*pac.fichas, *ctx.fichas_lidas],
        ),
        # D2 do dono (23/10/2026): a garantia Apple antes da compra só como o fato aprovado
        # (ou a resposta da equipe da mesma loja) diz, com a condição pelo anúncio.
        garantia_apple_pelo_fato=checagens.garantia_apple_da_proposta(
            p, entrada["texto"], ctx, base, fichas=[*pac.fichas, *ctx.fichas_lidas]
        ),
    )
    # Eval real de 21/10/2026: a lacuna que o código não preencheu ("{rastreio}"
    # sem rastreio) nunca fica no rascunho — a frase dela sai (ou, sem resposta
    # no resto, bloqueado e sem texto). As checagens leem o texto sem ela (a do
    # andamento lê o do modelo: sem as mesmas frases).
    lacunas_vazias = checagens.lacunas_no_texto(conferida.texto)
    conferida = checagens.sem_lacuna_vazia(conferida)
    # Integração r5: o rascunho de pessoa sem texto (o prompt: "nada verdadeiro a
    # dizer antes da pessoa") não é erro do validador — a ação é o motivo.
    conferida = checagens.sem_o_texto_vazio_de_pessoa(
        conferida, acao=saida.PESSOA if p.acao == saida.PESSOA or ctx.acoes_pessoa else p.acao
    )
    p_conferir = (
        p.model_copy(update={"texto": checagens.sem_as_frases_das_lacunas(p.texto, lacunas_vazias)})
        if lacunas_vazias
        else p
    )
    # As fichas do pacote e as que o `ver_anuncio` leu (com as variações dele):
    # a conferência de estoque e cor escolhe a do anúncio citado (revisão 2 de
    # 20/10/2026 — antes só a 1ª do pacote contava).
    # O bloco do pedido da conversa (revisão "furos" de 20/10/2026): o estado
    # e a origem que a resposta afirma são conferidos contra ele.
    ctx.pedido_da_conversa = pac.pedido
    chk = checagens.conferir(
        p_conferir, conferida, ctx, base, fichas=[*pac.fichas, *ctx.fichas_lidas], gatilho=gatilho
    )
    # A etiqueta Devolução da conversa (paridade com a Caixa Humano, spec 6.5
    # item 11): a `checagens.conferir` não recebe a conversa (contrato).
    ja = {pendencia.codigo for pendencia in chk.pendencias}
    da_etiqueta = [
        pendencia
        for pendencia in checagens.conferir_andamento(
            base.ctx,
            etiquetas=acao_pessoa.etiquetas_da_conversa(conversa),
            motivos=conferida.motivos,
            # Item 32 (22/10/2026): a devolução de rotina não segura pela etiqueta; D1: nem
            # o "responde e marca" (a etiqueta fica na Caixa Humano depois do envio).
            rotina=ctx.devolucao_de_rotina or ctx.responde_e_marca,
        )
        if pendencia.codigo not in ja
    ]
    da_etiqueta += [
        pendencia
        for pendencia in andamento_de_outro
        if pendencia.codigo not in ja and pendencia not in da_etiqueta
    ]
    if da_etiqueta:
        chk = checagens.Checagens(pendencias=[*chk.pendencias, *da_etiqueta])
    if base.modo == constantes.MODO_AUTO:
        # O portão (27/10/2026): a confiança do próprio agente (abaixo de 0,5 pessoa; até 0,8
        # aviso) e o assunto de produto sem a marca do anúncio nem a resposta da equipe (aviso).
        do_portao = checagens.avisos_do_portao(
            confianca=saida.confianca(p),
            assunto=ia.assunto_do_agente(conferida.categoria, p.acao, base.ids_categorias),
            produto_pelo_anuncio=ctx.produto_pelo_anuncio,
            produto_pela_equipe=bool(ctx.pares_citados(ctx.evidencias, de_produto=True)),
        )
        if do_portao:
            chk = checagens.Checagens(pendencias=[*chk.pendencias, *do_portao])
    ja_tratado = _ja_tratado_pela_loja(p, conferida, ctx, base, gatilho)
    if ja_tratado or _nao_pede_resposta(p, conferida, ctx, base):
        # "ok", "obrigado", aviso: nada a responder e nada que segure (ver o `responder`).
        if ja_tratado:
            ctx.acoes_modelo = []
        return Conferido(p, conferida, chk, sem_resposta=True, ja_tratado=ja_tratado)
    # O portão (27/10/2026): no automático, o que a 1ª camada do legado diz e não é duro
    # (a ficha no automático, a indução de assunto, o pedido não identificado, a frase da
    # lacuna tirada…) vira aviso; o `precisa_humano` fica com o duro e com o "pessoa" do modelo.
    conferida = checagens.separar_avisos(
        conferida, modo=base.modo, pessoa_do_modelo=bool(entrada["precisa_humano"])
    )
    final = checagens.aplicar(conferida, chk, acoes_pessoa=ctx.acoes_pessoa, modo=base.modo)
    _guardar_avisos(ctx, final)
    # A garantia Uranyx depois dos 90 dias (decisão 7 do dono): o envio confere
    # o texto sem a frase do caminho da Uranyx só com esta marca no rascunho.
    if checagens.caminho_uranyx_liberado(garantias) and checagens.sem_caminho_uranyx(
        final.texto
    ) != (final.texto or ""):
        ctx.isencoes = [checagens.ISENCAO_CAMINHO_URANYX]
    codigos = [pendencia.codigo for pendencia in chk.pendencias]
    return Conferido(p, conferida, chk, final=final, codigos=codigos)


async def responder(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    gatilho: AtendimentoMensagem,
    base: BaseDoGerar,
    *,
    ctx_extra: ExtraAgente | dict[str, Any] | None = None,
) -> ResultadoAgente:
    """Uma sugestão do agente para o `gatilho` (spec 3.1 passo 5 e 3.3). Não grava nada.

    `session` = a do `_gerar` (o laço faz commit nela ANTES de começar e só
    a usa de novo na `conferir_saida`); `base` = o que o `_gerar` já leu;
    `ctx_extra` = replay/eval/teste (`contexto.ExtraAgente`). Nunca levanta
    por causa do provedor, do teto, do prazo ou do formato: devolve o
    `ResultadoAgente` com a `parada` (spec 3.3.1, item 4):

    - teto (antes ou no meio) → sem rascunho (`PARADA_TETO`);
    - gatilho de código antes do modelo (instrução, só anexo depois do
      pedido de evidência) → rascunho sem texto para pessoa, sem custo;
    - `refusal` → bloqueado e SEM texto, para pessoa: "recusa do modelo
      (<categoria>)" — nunca um envio vazio (no Sonnet o servidor já refez a
      recusa "cyber"/"frontier_llm" no Sonnet 5; o Haiku não tem fallback);
    - `max_tokens` → 1 nova passada; de novo → bloqueado, cortado;
    - bateu `max_turnos` ainda pedindo ferramenta → "o agente não concluiu";
    - prazo cooperativo (`atendimento_agente_prazo_s`): na 1ª vez do gatilho
      sem rascunho (a próxima rodada tenta), na 2ª para pessoa;
    - erro do SDK → `erro_provedor` (o `_gerar` passa pelo `_falha`, como o
      legado);
    - resposta final que não é a `Proposta` → "fora do formato combinado".

    Depois das checagens, o `verificador` (20/10/2026) no rascunho que
    poderia sair sozinho: sem o "ok" dele, pessoa (`fatos.agente.verificador`).

    No replay (`modo_replay`): `as_of`, sem ida à loja, `forcar` na
    conferência (a loja já respondeu depois) e nenhuma escrita além do que
    volta no resultado (nem o contador do prazo).
    """
    # O cliente próprio da API vive até o fim (o verificador usa o mesmo do laço).
    async with contextlib.AsyncExitStack() as pilha:
        return await _responder(session, conversa, gatilho, base, ctx_extra=ctx_extra, pilha=pilha)


async def _responder(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    gatilho: AtendimentoMensagem,
    base: BaseDoGerar,
    *,
    ctx_extra: ExtraAgente | dict[str, Any] | None,
    pilha: contextlib.AsyncExitStack,
) -> ResultadoAgente:
    """O corpo do `responder`; `pilha` fecha o cliente próprio quando ele termina."""
    ext = contexto.ler_extra(ctx_extra)
    s = get_settings()
    modelo = ext.get("modelo") or s.atendimento_agente_modelo
    effort = ext.get("effort") or s.atendimento_agente_effort
    if effort not in EFFORTS:
        logger.warning("atendimento_agente_effort_invalido", effort=str(effort)[:20])
        effort = EFFORT_PADRAO
    max_turnos = ext.get("max_turnos") or s.atendimento_agente_max_turnos
    ctx = ContextoAgente.da_conversa(conversa, gatilho, modo=base.modo, nomes=base.nomes, extra=ext)
    ctx.modelo, ctx.effort = modelo, effort
    t0 = _relogio()
    prazo = t0 + float(ext.get("prazo_s") or s.atendimento_agente_prazo_s)
    ex = _Execucao(
        ctx=ctx,
        t0=t0,
        sistema_hash=prompt.hash_do_sistema(conversa.plataforma, base.categorias),
        manual_hash=ia.manual_hash(ia.organizar_manual(list(base.regras), None)),
    )

    try:
        await custo.conferir_teto(
            conversa_id=conversa.id,
            uso=ctx.uso,
            replay=ctx.modo_replay,
            teto_conversa=ctx.teto_conversa_usd,
        )
    except custo.TetoEstourado as e:
        return ex.sem_rascunho(PARADA_TETO, teto_qual=e.qual)
    cliente = ext.get("cliente")
    chave = None if cliente is not None else agente_config.chave_do_agente(s)
    if cliente is None and not chave:
        # O `_gerar` só chama com a chave (`agente_ligado`); sem ela, como o
        # legado sem chave: passageiro, nada gravado.
        return ex.sem_rascunho(PARADA_PROVEDOR, erro_provedor=ia.ErroProvedor(MOTIVO_SEM_CHAVE))

    pac = await pacote.montar_pacote(session, conversa, gatilho, base, ctx)
    gat = await acao_pessoa.gatilhos_de_codigo(
        session, conversa, gatilho, base, ctx, postado=pac.postado
    )
    if any(g.antes_do_modelo for g in gat):
        motivos = list(dict.fromkeys([*acao_pessoa.motivos(gat), *base.motivos]))
        return ex.sem_texto(PARADA_SEM_AGENTE, "; ".join(motivos) or MOTIVO_ANTES_DO_MODELO)

    await _soltar_a_sessao(session, ctx)
    argumentos = argumentos_do_laco(
        modelo=modelo,
        effort=effort,
        max_turnos=max_turnos,
        sistema=prompt.sistema(conversa.plataforma, base.categorias),
        tools=ferramentas.ferramentas(ctx, base, conversa),
        texto_do_pacote=pac.texto,
    )
    laco = {"conversa_id": conversa.id, "prazo": prazo, "max_turnos": max_turnos}
    if cliente is None:
        # O próprio: fechado pela `pilha` do `responder`, depois do verificador.
        cliente = await pilha.enter_async_context(
            anthropic.AsyncAnthropic(
                api_key=chave,
                base_url=ia.BASE_CLAUDE,
                timeout=TIMEOUT,
                max_retries=MAX_RETRIES,
            )
        )
    try:
        volta = await _laco(cliente, argumentos, ctx, **laco)
    except custo.TetoEstourado as e:
        return ex.sem_rascunho(PARADA_TETO, teto_qual=e.qual)
    except anthropic.AnthropicError as e:
        erro = erro_do_provedor(e, modelo)
        logger.warning(
            "atendimento_agente_provedor",
            erro=erro.motivo,
            limite=erro.limite,
            definitivo=erro.definitivo,
            sem_credito=erro.sem_credito,
        )
        if isinstance(e, anthropic.APITimeoutError):
            # O servidor pode ter gerado (e cobrado) e o `usage` não volta: o
            # custo provável entra no teto; a 2ª vez do gatilho vai para pessoa.
            await custo.registrar_estimativa(
                custo.estimativa_de_requisicao_perdida(
                    modelo, ctx.max_tokens_da_passada or MAX_TOKENS
                ),
                conversa_id=conversa.id,
                uso=ctx.uso,
                replay=ctx.modo_replay,
            )
            if not ctx.modo_replay and (
                await _vezes_do_prazo(gatilho.id or conversa.id, chave_modelo=CHAVE_DEMORA) > 1
            ):
                return ex.sem_texto(PARADA_PROVEDOR, MOTIVO_PROVEDOR_DEMOROU)
        elif (
            erro.codigo == ERRO_PASSAGEIRO
            and not ctx.modo_replay
            and await _vezes_do_prazo(gatilho.id or conversa.id, chave_modelo=CHAVE_FALHA)
            >= VEZES_FALHA_ATE_PESSOA
        ):
            # O mesmo gatilho falhou 3 vezes (5xx/529/rede): pessoa, sem custo a mais.
            return ex.sem_texto(PARADA_PROVEDOR, MOTIVO_PROVEDOR_FALHOU)
        return ex.sem_rascunho(PARADA_PROVEDOR, erro_provedor=erro)

    ultima = volta.ultima
    parada = getattr(ultima, "stop_reason", None)
    if parada == "refusal":
        categoria = getattr(getattr(ultima, "stop_details", None), "category", None)
        return ex.sem_texto(
            PARADA_RECUSA, MOTIVO_RECUSA.format(categoria=categoria or "sem categoria")
        )
    if volta.estourou_prazo:
        if ctx.modo_replay or await _vezes_do_prazo(gatilho.id or conversa.id) <= 1:
            return ex.sem_rascunho(PARADA_PRAZO)
        return ex.sem_texto(PARADA_PRAZO, MOTIVO_PRAZO)
    if parada in _CORTADA:
        return ex.sem_texto(PARADA_MAX_TOKENS, MOTIVO_CORTADO)
    if ultima is None or parada in _CONTINUA:
        return ex.sem_texto(PARADA_MAX_ITERACOES, MOTIVO_NAO_CONCLUIU)
    p = proposta_da_mensagem(ultima)
    if p is None:
        return ex.sem_texto(PARADA_FORMATO, MOTIVO_FORA_DO_FORMATO)
    c = await conferir_proposta(session, conversa, gatilho, base, ctx, p, pac)
    p, conferida = c.proposta, c.conferida
    if c.sem_resposta:
        # "ok", "obrigado", aviso: nada a responder e nada que segure — o
        # rascunho DESCARTADO marca o gatilho como tratado (o cron não volta
        # nele) e não entra na Caixa Humano (revisão de 20/10/2026). O turno só
        # de reconhecimento depois de a loja tratar (Kia ao vivo, 21/10/2026):
        # a ação de pessoa que o modelo pediu de novo sai junto (a equipe já fez
        # ou prometeu; o acompanhamento é dela).
        ja_tratado = c.ja_tratado
        ctx.parada = PARADA_SEM_RESPOSTA
        ex._ms()
        return ex.pronto(
            ResultadoAgente(
                parada=PARADA_SEM_RESPOSTA,
                gravar=True,
                categoria=conferida.categoria,
                confianca=saida.confianca(p),
                precisa_humano=False,
                motivo=MOTIVO_JA_TRATADO if ja_tratado else MOTIVO_SEM_RESPOSTA,
                validador_ok=True,
                validador_erros=[],
                status=constantes.RASCUNHO_DESCARTADO,
                acoes_pessoa=[],
                fatos_agente=ctx.para_fatos(),
                modelo=getattr(ultima, "model", None) or ctx.modelo,
                manual_hash=ex.manual_hash,
                tokens_entrada=ctx.uso.tokens_entrada,
                tokens_saida=ctx.uso.saida,
                custo_usd=ctx.uso.custo_usd,
                proposta=p,
                conferida=conferida,
            )
        )
    final, codigos = c.final, c.codigos
    # A 2ª camada (20/10/2026): o verificador, só no rascunho que poderia sair
    # sozinho, com o MESMO cliente e o que sobra do prazo; sem o "ok", pessoa.
    conf = await verificador.no_rascunho(
        session,
        conversa,
        gatilho,
        base,
        ctx,
        p,
        final,
        cliente=cliente,
        prazo_s=_prazo_do_verificador(prazo, forcar=base.forcar),
        pediu_evidencia=pac.pediu_evidencia,
        checagens_feitas=codigos,
        soltar_sessao=lambda: _soltar_a_sessao(session, ctx),
    )
    final = conf.final
    _guardar_avisos(ctx, final)
    if conf.codigo:
        codigos.append(conf.codigo)
    fatos_do_verificador = {"verificador": conf.registro} if conf.registro is not None else {}
    ctx.parada = PARADA_FIM
    ex._ms()
    return ex.pronto(
        ResultadoAgente(
            parada=PARADA_FIM,
            gravar=True,
            texto=final.texto or None,
            categoria=final.categoria,
            confianca=saida.confianca(p),
            # A ação de pessoa do código sempre vence (spec 6.3), mesmo se a
            # checagem esquecer de marcar.
            precisa_humano=final.precisa_humano or bool(ctx.acoes_pessoa),
            motivo=final.motivo,
            validador_ok=final.validador_ok,
            validador_erros=list(final.erros),
            status=final.status,
            acoes_pessoa=ctx.acoes_pessoa,
            fatos_agente=ctx.para_fatos(),
            modelo=getattr(ultima, "model", None) or ctx.modelo,
            manual_hash=ia.manual_hash(ia.organizar_manual(list(base.regras), final.categoria)),
            tokens_entrada=ctx.uso.tokens_entrada,
            tokens_saida=ctx.uso.saida,
            # O laço + o verificador (o uso dele fica à parte, no registro).
            custo_usd=ctx.uso.custo_usd + conf.custo_usd,
            proposta=p,
            conferida=final,
        ),
        checagens=codigos,
        **fatos_do_verificador,
    )
