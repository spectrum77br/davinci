"""Defesa da Fraude escrita pela IA (Vinicius, 16/11/2026).

"painel fraude tem que abrir chamado. não precisa falar que não recebi — às vezes
o cliente fala que chegou um sabonete, não vamos abrir chamado falando que não
recebi a devolução, vamos ter que pensar e escrever uma defesa mostrando o vídeo
que enviamos."

No caso SEM devolução o produto não volta: o chamado é sempre uma DEFESA contra o
que o comprador alegou, com o vídeo da embalagem como prova. Antes, o motivo saía
de palavras-chave e o texto de um modelo fixo; agora a IA lê a alegação (o motivo
padronizado da plataforma e o texto livre do comprador), diz o que ele alega e
escreve a defesa daquele caso. O sistema lança com o motivo que corresponde à
alegação e a defesa vai como "Observação pro chamado" (o texto que a plataforma
recebe, junto dos fatos da entrega e do link do vídeo).

A IA não assiste ao vídeo: a defesa diz o que o vídeo da embalagem prova (o produto
do pedido conferido, embalado e lacrado antes do envio), sem inventar detalhe.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import pydantic
import structlog

logger = structlog.get_logger()

TIMEOUT_S = 45.0
MAX_TOKENS = 4096
# A TikTok corta o comentário em 500 bytes e os fatos (abertura, entrega com
# rastreio, link do vídeo) vêm antes: ~320 bytes. A defesa cabe no que sobra, sem o
# pedido final (o texto de cada plataforma já termina com ele).
MAX_CHARS_DEFESA = 150
MAX_CHARS_RESUMO = 140
MAX_CHARS_PESSOA = 300

# O que o comprador alega → o motivo do lançamento. 16/11/2026 (Vinicius, 307238 —
# "O item não corresponde à descrição"): "teria que responder já, mostrando vídeo e
# falando que enviamos certinho" — diferente do anúncio e dano/defeito também
# viram defesa com o vídeo. Só o que não se entende fica com a pessoa.
ALEGACOES: dict[str, str | None] = {
    "vazio_ou_outro_produto": "Golpe",
    "faltou_item": "Item faltando",
    "nao_recebeu": "Não recebido",
    "diferente_do_anuncio": "Golpe",
    "danificado_ou_defeito": "Danificado (Outros)",
    "outra": None,
}

# Em que pé o caso está na plataforma (decide como a defesa termina).
RESPONDER = "responder"
EM_ANALISE = "em_analise"
PAGO = "pago"

SISTEMA = """Você escreve a defesa de uma loja que vende em marketplaces (Shopee, TikTok \
Shop e Mercado Livre) em casos de SÓ REEMBOLSO: o comprador pede o dinheiro de volta e \
fica com o produto — nenhum pacote volta para a loja. A loja grava em vídeo a conferência \
e a embalagem de cada pedido antes de enviar; esse vídeo é a prova da loja e o link dele \
vai junto do texto (o sistema coloca o link, você não escreve link).

Você recebe o que o comprador alegou (o motivo padronizado da plataforma e o texto livre dele), \
os produtos do pedido, a entrega do pedido ao comprador e em que pé o caso está.

Responda:
1. alegacao — o que o comprador alega, numa destas opções:
   - vazio_ou_outro_produto: recebeu a caixa vazia, violada, ou outro produto / um objeto \
qualquer no lugar do que comprou (sabonete, pedra, papel, outro aparelho…).
   - faltou_item: recebeu o pedido, mas faltou uma peça, acessório ou unidade.
   - nao_recebeu: diz que o pedido não chegou.
   - diferente_do_anuncio: diz que o produto não corresponde à descrição/ao anúncio (modelo, \
cor, tamanho, características diferentes).
   - danificado_ou_defeito: chegou quebrado, amassado, riscado, não liga, com defeito.
   - outra: arrependimento, desistência, ou não dá para entender o que alega.
   Quando o motivo padronizado e o texto do comprador não combinam, vale o texto; se ainda \
assim houver dúvida, escolha "outra".
   Se a entrada trouxer uma INSTRUÇÃO da equipe, siga-a: ela manda contestar e diz o que \
defender. Escolha em alegacao a opção que melhor corresponde ao que a instrução manda \
contestar ("outra" só se nem a instrução der pra entender) e use o que ela diz na defesa.
2. resumo_alegacao — o que o comprador alega, em português simples, numa frase curta.
3. defesa — o trecho que a plataforma vai ler no meio do texto da loja (o sistema já põe a \
identificação do pedido, a entrega, o link do vídeo e o pedido final). Regras:
   - Português do Brasil, educado e firme, primeira pessoa do plural ("enviamos").
   - Diga em poucas palavras o que o comprador alega e o que o vídeo da embalagem mostra: o \
produto do pedido sendo conferido, embalado e lacrado antes do envio. Diferente do anúncio: \
enviamos exatamente o produto anunciado no pedido. Dano/defeito: o produto saiu conferido e \
em perfeito estado, bem embalado.
   - NUNCA fale em devolução, pacote de volta ou "não recebemos": neste caso nada volta.
   - Você não assiste ao vídeo: não descreva cor, horário, peso ou detalhe que não está na \
entrada. Use só fatos da entrada; não invente datas nem números.
   - Sem dados pessoais do comprador, sem link e SEM o pedido final ("pedimos que…").
   - No máximo 150 caracteres.
4. precisa_pessoa — false quando a loja pode se defender sozinha com o vídeo (vazio, outro \
produto, faltou item, diferente do anúncio, dano/defeito); true quando: a alegação é outra; o \
comprador diz que não recebeu e a entrega não está confirmada; a entrega NÃO aconteceu \
(extravio — o caso é pedir compensação, não contestar); ou a alegação e os fatos não batem.
5. por_que — quando precisa_pessoa é true, por que uma pessoa precisa olhar (uma frase curta); \
senão, vazio."""


class Resposta(pydantic.BaseModel):
    alegacao: str
    resumo_alegacao: str = ""
    defesa: str = ""
    precisa_pessoa: bool = False
    por_que: str = ""


def esquema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "alegacao": {"type": "string", "enum": list(ALEGACOES)},
            "resumo_alegacao": {"type": "string"},
            "defesa": {"type": "string"},
            "precisa_pessoa": {"type": "boolean"},
            "por_que": {"type": "string"},
        },
        "required": ["alegacao", "resumo_alegacao", "defesa", "precisa_pessoa", "por_que"],
        "additionalProperties": False,
    }


def _limpo(texto: str | None, limite: int) -> str:
    return " ".join(str(texto or "").split())[:limite]


def _cortar_frase(texto: str, limite: int) -> str:
    """Até `limite` caracteres, terminando numa frase ou palavra inteira. PURA."""
    texto = " ".join(str(texto or "").split())
    if len(texto) <= limite:
        return texto
    corte = texto[:limite]
    fim = corte.rfind(". ")
    if fim >= limite // 2:
        return corte[: fim + 1]
    return corte[: corte.rfind(" ")].rstrip(" ,;:") + "."


@dataclass(frozen=True)
class Fatos:
    """O que a IA lê. `situacao` = RESPONDER | EM_ANALISE | PAGO."""

    plataforma: str
    situacao: str
    codigo: str = ""
    texto: str = ""
    produtos: tuple[str, ...] = ()
    # A ida chegou ao comprador? True / False (não entregue) / None (não se sabe).
    ida_entregue: bool | None = None
    entregue_em: str = ""  # "10/11" (data da entrega da ida, quando há)
    transportadora: str = ""
    comprador_mandou_prova: bool | None = None
    prazo: str = ""  # "18/11 14:00" (até quando a plataforma espera a resposta)
    # "Abrir chamado: Sim" + "Instrução IA" da equipe (16/11/2026): vale mais que a
    # leitura da IA — pode mandar contestar o que ela deixaria pra pessoa.
    instrucao: str = ""


_SITUACAO_PT = {
    RESPONDER: "a plataforma ESPERA a nossa resposta",
    EM_ANALISE: "a plataforma está ANALISANDO o caso (não pediu a nossa resposta)",
    PAGO: "a plataforma JÁ REEMBOLSOU o comprador",
}


def entrada(f: Fatos) -> str:
    """O texto que vai pra IA. PURA."""
    situacao = _SITUACAO_PT.get(f.situacao, f.situacao)
    if f.situacao == RESPONDER and f.prazo:
        situacao += f" até {f.prazo}"
    if f.ida_entregue is True:
        entrega = "entregue ao comprador" + (f" em {f.entregue_em}" if f.entregue_em else "")
        if f.transportadora:
            entrega += f" pela {f.transportadora}"
    elif f.ida_entregue is False:
        entrega = "NÃO foi entregue ao comprador (a transportadora não concluiu a entrega)"
    else:
        entrega = "entrega não confirmada"
    prova = {True: "sim", False: "não"}.get(f.comprador_mandou_prova, "não se sabe")  # type: ignore[arg-type]
    linhas = [
        f"Plataforma: {f.plataforma}",
        f"Situação do caso: {situacao}",
        "Produtos do pedido:",
        *[f"- {_limpo(p, 120)}" for p in f.produtos or ("não informado",)],
        f'Motivo que o comprador escolheu: "{_limpo(f.codigo, 120) or "—"}"',
        f'Texto do comprador: "{_limpo(f.texto, 600) or "—"}"',
        f"O comprador mandou foto ou vídeo: {prova}",
        f"Entrega do pedido: {entrega}",
        "Vídeo da embalagem: temos (o link vai junto do texto)",
    ]
    if (f.instrucao or "").strip():
        linhas.append(f'INSTRUÇÃO da equipe: "{_limpo(f.instrucao, 1200)}"')
    return "\n".join(linhas)


def argumentos(texto: str, *, modelo: str) -> dict[str, Any]:
    """Os argumentos da chamada (mesmo formato da conferência do motivo). PURA."""
    return {
        "model": modelo,
        "max_tokens": MAX_TOKENS,
        "system": SISTEMA,
        "messages": [{"role": "user", "content": texto}],
        "output_config": {
            "effort": "medium",
            "format": {"type": "json_schema", "schema": esquema()},
        },
        "betas": ["server-side-fallback-2026-07-01"],
        "fallbacks": "default",
    }


def resposta_da_mensagem(mensagem: Any) -> Resposta | None:
    """A `Resposta` (o último bloco de texto que o é inteiro), ou None. PURA."""
    if getattr(mensagem, "stop_reason", None) not in ("end_turn", "stop_sequence"):
        return None
    for bloco in reversed(list(getattr(mensagem, "content", None) or [])):
        if getattr(bloco, "type", None) != "text" or not getattr(bloco, "text", None):
            continue
        try:
            r = Resposta.model_validate_json(bloco.text)
        except pydantic.ValidationError:
            continue
        if r.alegacao not in ALEGACOES:
            continue
        return r
    return None


Perguntar = Callable[[str], Awaitable[tuple["Resposta | None", "str | None"]]]


async def perguntar(texto: str) -> tuple[Resposta | None, str | None]:
    """Uma requisição à IA. (resposta ou None, modelo). Nunca levanta."""
    import anthropic

    from app.config import get_settings
    from app.services.atendimento import agente_config, ia

    settings = get_settings()
    chave_api = agente_config.chave_do_agente(settings)
    if not chave_api:
        return None, None
    modelo = settings.devolucao_conferencia_modelo
    try:
        async with anthropic.AsyncAnthropic(
            api_key=chave_api, base_url=ia.BASE_CLAUDE, max_retries=2
        ) as cliente:
            mensagem = await cliente.beta.messages.create(
                **argumentos(texto, modelo=modelo),
                timeout=anthropic.Timeout(TIMEOUT_S, connect=10.0),
            )
    except anthropic.AnthropicError as e:
        logger.warning("devolucao_fraude_defesa_erro", err=type(e).__name__)
        return None, modelo
    return resposta_da_mensagem(mensagem), str(getattr(mensagem, "model", None) or modelo)


@dataclass(frozen=True)
class Decisao:
    """O que o robô faz: `motivo` + `defesa` = lança e abre o chamado; `pessoa` =
    fica com a pessoa (o porquê); tudo vazio = a IA não respondeu (tenta de novo)."""

    motivo: str | None = None
    defesa: str = ""
    resumo: str = ""
    pessoa: str | None = None


NAO_RECEBEU_SEM_ENTREGA = (
    "o comprador diz que não recebeu e a entrega do pedido não está confirmada"
)


def decidir(f: Fatos, r: Resposta | None) -> Decisao:
    """Junta a leitura da IA com as travas que não dependem dela. PURA."""
    if r is None:
        return Decisao()
    resumo = _limpo(r.resumo_alegacao, MAX_CHARS_RESUMO)
    motivo = ALEGACOES.get(r.alegacao)
    if f.ida_entregue is False:
        return Decisao(
            resumo=resumo,
            pessoa="a entrega do pedido NÃO aconteceu (extravio: pedir compensação, não contestar)",
        )
    por_que = _limpo(r.por_que, MAX_CHARS_PESSOA)
    if motivo is None:
        return Decisao(
            resumo=resumo,
            pessoa=por_que or "é um tipo de alegação que o vídeo da embalagem não rebate sozinho",
        )
    if motivo == "Não recebido" and f.ida_entregue is not True:
        return Decisao(resumo=resumo, pessoa=NAO_RECEBEU_SEM_ENTREGA)
    if r.precisa_pessoa:
        return Decisao(resumo=resumo, pessoa=por_que or "a IA pediu que uma pessoa confira")
    defesa = _cortar_frase(r.defesa, MAX_CHARS_DEFESA)
    if len(defesa) < 20:
        return Decisao(resumo=resumo, pessoa="a IA não escreveu a defesa")
    return Decisao(motivo=motivo, defesa=defesa, resumo=resumo)


async def analisar(f: Fatos, pergunta: Perguntar | None = None) -> Decisao:
    """Pergunta à IA e decide. Sem alegação lida (a TikTok devolve vazio quando a API
    falha), nem pergunta: tenta de novo na próxima rodada."""
    if not (f.codigo or "").strip() and not (f.texto or "").strip():
        return Decisao()
    if f.ida_entregue is False:
        # Já se sabe que é da pessoa: a IA só daria o resumo.
        return decidir(f, Resposta(alegacao="outra", resumo_alegacao=_limpo(f.texto, 140)))
    r, _modelo = await (pergunta or perguntar)(entrada(f))
    return decidir(f, r)


def produtos_de(pecas: Sequence[dict]) -> tuple[str, ...]:
    """Os nomes dos produtos (sem repetir). PURA."""
    return tuple(
        dict.fromkeys(
            n for p in pecas if (n := _limpo(p.get("produtos") or p.get("sku") or "", 120))
        )
    )
