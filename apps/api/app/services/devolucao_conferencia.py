"""Conferência do motivo da devolução antes de a disputa sair.

Vinicius, 16/11/2026: "hoje às vezes as pessoas lançam errado, colocam
extraviado e colocam que recebeu um sabonete dentro — será que o painel
consegue analisar o real motivo antes de abrir chamado?" e, vendo o desenho,
"se não bate, segurar e perguntar".

A disputa automática sai ~1 min depois do lançamento e é tiro único na
plataforma. Antes de ela sair (`chamados_devolucao._disparar`), uma IA lê o
que foi lançado — motivo, condição e observação de cada peça, mais a
observação do pedido — e diz se o motivo bate. Se NÃO bate, a abertura fica
`segurada` e a pessoa decide na janela da devolução (trocar o motivo e mandar,
mandar como está ou não abrir). Se a IA não responde (sem chave, fora do ar,
recusa), a disputa segue como sempre seguiu — a conferência nunca é o motivo
de uma disputa perder o prazo.

O resultado fica em `devolucao_rastreio.conferencia` com a CHAVE do que ela
leu: a mesma entrada não é conferida duas vezes, e mudar motivo, condição ou
observação faz conferir de novo.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pydantic
import structlog

logger = structlog.get_logger()

STATUS_SEGURADA = "segurada"
ERRO_MOTIVO_DIVERGENTE = "motivo_divergente"
# Marca que a Fraude automática (services/devolucao_fraude_auto) grava na
# Observação do pedido quando lança sozinha: lançamento sem pessoa só sai com a
# IA conferindo — sem resposta dela, segura (16/11/2026, revisão).
MARCA_ROBO = "lançado sozinho pela Fraude automática"

# Os nomes que a tela mostra (DevolucaoLancar.vue). Perdimento grava `Sucata`.
MOTIVOS = (
    "Bloqueado",
    "Golpe",
    "Item faltando",
    "Dano funcional / Não funciona",
    "Item Incorreto",
    "Não recebido",
    "Danificado (Outros)",
)
CONDICOES = ("Novo", "Usado", "Perdimento", "Extraviado", "Não devolvido", "Trocado", "Manutenção")

TIMEOUT_S = 45.0
MAX_TOKENS = 4096
MAX_CHARS_OBS = 600
MAX_CHARS_EXPLICACAO = 400

SISTEMA = """Você confere lançamentos de devolução de uma loja que vende em marketplaces \
(Mercado Livre, Shopee, TikTok Shop, Amazon). Para cada pedido devolvido, a equipe registra, \
por produto, o MOTIVO da devolução, a CONDIÇÃO em que o produto voltou e uma observação. \
Logo depois, o sistema manda sozinho uma contestação para a plataforma com esse motivo — e a \
plataforma só aceita uma contestação por pedido. Seu trabalho é dizer se o motivo e a condição \
fazem sentido com o que a própria equipe escreveu, ANTES de a contestação sair.

O que cada MOTIVO diz à plataforma:
- Não recebido: o pacote de volta NÃO chegou até a loja.
- Golpe: o pacote chegou, mas com outro produto, um objeto qualquer ou vazio no lugar do \
que foi enviado.
- Item faltando: o pacote chegou, mas faltam itens que foram enviados.
- Danificado (Outros): o produto voltou danificado / quebrado.
- Dano funcional / Não funciona: o produto voltou e não funciona.
- Item Incorreto: o produto devolvido é outro modelo/variação.
- Bloqueado: o produto voltou travado por senha/conta do comprador.

O que cada CONDIÇÃO quer dizer: Novo e Usado = o produto voltou e entra no estoque; Perdimento = \
voltou sem conserto; Extraviado = o pacote se perdeu no caminho e nada chegou; Não devolvido = o \
comprador não devolveu o produto; Trocado = voltou outro produto; Manutenção = foi para o técnico.

Caso SEM devolução (condição "Não devolvido" — o comprador pediu só o reembolso e o produto \
não volta): aqui o motivo é a ALEGAÇÃO do comprador que a loja está contestando com o vídeo da \
embalagem, não o estado de um pacote que voltou. "Não recebido" = o comprador diz que não \
recebeu; "Golpe" = diz que recebeu vazio, violado ou outro produto; "Item faltando" = diz que \
faltou item. Nada disso contradiz "Não devolvido". A Observação do pedido pode trazer o que o \
comprador alegou ("Fraude automática: o comprador alega …"): confira o motivo contra essa \
alegação.

Regras:
- Marque bate=false SÓ quando houver contradição clara entre o lançamento e o que está escrito \
(ex.: motivo "Não recebido" ou condição "Extraviado", mas a observação diz que a caixa foi aberta \
e veio outra coisa dentro). Observação vazia, curta ou vaga = bate=true.
- Recados internos na observação (prazos, "aguardar", senha, quem fez o quê) não são contradição.
- Quando bate=false, sugira o motivo e a condição que fazem sentido, usando exatamente um dos \
nomes das listas; deixe vazio o que não precisa mudar.
- A explicação é para a pessoa da equipe: português simples, no máximo 2 frases curtas, sem \
repetir dados do cliente."""


class Veredito(pydantic.BaseModel):
    bate: bool
    motivo_sugerido: str = ""
    condicao_sugerida: str = ""
    explicacao: str = ""


def esquema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "bate": {"type": "boolean"},
            "motivo_sugerido": {"type": "string", "enum": ["", *MOTIVOS]},
            "condicao_sugerida": {"type": "string", "enum": ["", *CONDICOES]},
            "explicacao": {"type": "string"},
        },
        "required": ["bate", "motivo_sugerido", "condicao_sugerida", "explicacao"],
        "additionalProperties": False,
    }


@dataclass(frozen=True)
class Peca:
    sku: str
    produto: str
    motivo: str
    condicao: str
    observacao: str


def _limpo(texto: str | None, limite: int = MAX_CHARS_OBS) -> str:
    return " ".join(str(texto or "").split())[:limite]


def condicao_na_tela(valor: str | None) -> str:
    return "Perdimento" if (valor or "").strip() == "Sucata" else (valor or "").strip()


def pecas_de(linhas: Sequence[Any]) -> list[Peca]:
    """As peças lançadas do pedido, no formato que a IA lê. PURA."""
    return [
        Peca(
            sku=_limpo(getattr(d, "sku", ""), 80),
            produto=_limpo(getattr(d, "produtos", ""), 120),
            motivo=_limpo(getattr(d, "motivo_devolucao", ""), 80),
            condicao=condicao_na_tela(getattr(d, "condicao_produto", "")),
            observacao=_limpo(getattr(d, "observacao", "")),
        )
        for d in linhas
    ]


def chave(pecas: Sequence[Peca], observacao_pedido: str | None) -> str:
    """O que a IA leu, resumido: mesma chave = mesma resposta. PURA."""
    base = json.dumps(
        sorted([p.sku.lower(), p.motivo.lower(), p.condicao.lower(), p.observacao] for p in pecas)
        + [_limpo(observacao_pedido)],
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:32]


def entrada(pecas: Sequence[Peca], observacao_pedido: str | None, plataforma: str | None) -> str:
    """O texto que vai pra IA. PURA."""
    linhas = [f"Plataforma: {plataforma or 'não informada'}", "", "Produtos lançados:"]
    for p in pecas:
        linhas.append(
            f'- {p.sku or "sem SKU"} ({p.produto or "sem nome"}): motivo "{p.motivo or "—"}"; '
            f'condição "{p.condicao or "—"}"; observação: "{p.observacao or "—"}"'
        )
    obs = _limpo(observacao_pedido)
    linhas += ["", f'Observação do pedido (interna, não vai pra plataforma): "{obs or "—"}"']
    return "\n".join(linhas)


def argumentos(texto: str, *, modelo: str) -> dict[str, Any]:
    """Os argumentos da chamada. PURA. Sem `thinking`/`temperature`/`tool_choice`
    (os modelos 5.5 recusam); o formato JSON em `output_config`, com o fallback
    do servidor caso o modelo recuse por política."""
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


def veredito_da_mensagem(mensagem: Any) -> Veredito | None:
    """O `Veredito` da resposta (o último bloco de texto que o é inteiro), ou None. PURA."""
    if getattr(mensagem, "stop_reason", None) not in ("end_turn", "stop_sequence"):
        return None
    for bloco in reversed(list(getattr(mensagem, "content", None) or [])):
        if getattr(bloco, "type", None) != "text" or not getattr(bloco, "text", None):
            continue
        try:
            v = Veredito.model_validate_json(bloco.text)
        except pydantic.ValidationError:
            continue
        if v.motivo_sugerido not in ("", *MOTIVOS):
            v.motivo_sugerido = ""
        if v.condicao_sugerida not in ("", *CONDICOES):
            v.condicao_sugerida = ""
        v.explicacao = _limpo(v.explicacao, MAX_CHARS_EXPLICACAO)
        return v
    return None


Perguntar = Callable[[str], Awaitable[tuple[Veredito | None, str | None]]]


async def perguntar(texto: str) -> tuple[Veredito | None, str | None]:
    """Uma requisição à IA. Devolve (veredito ou None, modelo usado). Nunca levanta:
    sem chave, fora do ar ou recusa = (None, ...) e a disputa segue sem conferir."""
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
        logger.warning("devolucao_conferencia_erro", err=type(e).__name__)
        return None, modelo
    return veredito_da_mensagem(mensagem), str(getattr(mensagem, "model", None) or modelo)


def ligada() -> bool:
    from app.config import get_settings
    from app.services.atendimento import agente_config

    settings = get_settings()
    return bool(settings.devolucao_conferencia_ligada) and bool(
        agente_config.chave_do_agente(settings)
    )


def segurado_sem_ia(
    pecas: Sequence[Peca], k: str, agora: datetime, *, modelo: str | None = None
) -> dict[str, Any]:
    """O que fica gravado quando o lançamento do robô não pôde ser conferido pela
    IA: aparece como "segurado" na janela, com os botões de sempre (mandar / não
    abrir). PURA."""
    return {
        "chave": k,
        "em": agora.isoformat(),
        "modelo": modelo,
        "motivo_lancado": next((p.motivo for p in pecas if p.motivo), ""),
        "condicao_lancada": next((p.condicao for p in pecas if p.condicao), ""),
        "resultado": "nao_bate",
        "motivo_sugerido": "",
        "condicao_sugerida": "",
        "explicacao": (
            "Lançado sozinho pela Fraude automática e a IA não conferiu (sem resposta ou "
            "desligada). Confira o motivo e mande — ou não abra."
        ),
    }


@dataclass(frozen=True)
class Resultado:
    """O que fazer com a disputa: `segurar` (não manda) ou seguir. `evento` vai
    pro histórico do chamado quando a conferência rodou agora."""

    segurar: bool
    conferencia: dict[str, Any] | None
    evento: str | None = None


async def conferir(
    *,
    pecas: Sequence[Peca],
    observacao_pedido: str | None,
    plataforma: str | None,
    anterior: dict[str, Any] | None,
    agora: datetime | None = None,
    pergunta: Perguntar | None = None,
    exigir_ok: bool = False,
) -> Resultado:
    """Decide se a disputa pode sair. Reaproveita a resposta anterior quando a
    entrada é a mesma (inclusive a decisão da pessoa — "mandar como está" vale
    até alguém mudar o lançamento). `exigir_ok` (lançado pelo robô): IA sem
    resposta segura em vez de deixar sair."""
    agora = agora or datetime.now(UTC)
    k = chave(pecas, observacao_pedido)
    anterior = anterior or {}
    if anterior.get("chave") == k:
        res = anterior.get("resultado")
        if res == "nao_bate":
            return Resultado(segurar=True, conferencia=None)
        if res in ("bate", "aceito") or (res == "erro" and not exigir_ok):
            return Resultado(segurar=False, conferencia=None)
    veredito, modelo = await (pergunta or perguntar)(entrada(pecas, observacao_pedido, plataforma))
    base = {
        "chave": k,
        "em": agora.isoformat(),
        "modelo": modelo,
        "motivo_lancado": next((p.motivo for p in pecas if p.motivo), ""),
        "condicao_lancada": next((p.condicao for p in pecas if p.condicao), ""),
    }
    if veredito is None and exigir_ok:
        return Resultado(
            segurar=True,
            conferencia=segurado_sem_ia(pecas, k, agora, modelo=modelo),
            evento=(
                "A IA não respondeu e este lançamento foi feito sozinho pela Fraude "
                "automática: a contestação espera alguém conferir na janela da devolução."
            ),
        )
    if veredito is None:
        return Resultado(
            segurar=False,
            conferencia={**base, "resultado": "erro"},
            evento=(
                "A conferência do motivo não rodou (IA sem resposta) — "
                "a contestação seguiu sem conferir."
            ),
        )
    if veredito.bate:
        return Resultado(
            segurar=False,
            conferencia={**base, "resultado": "bate", "explicacao": veredito.explicacao},
            evento="Conferido pela IA antes de mandar: o motivo bate com o que foi lançado.",
        )
    sugestao = " · ".join(
        x
        for x in (
            f'motivo "{veredito.motivo_sugerido}"' if veredito.motivo_sugerido else "",
            f'condição "{veredito.condicao_sugerida}"' if veredito.condicao_sugerida else "",
        )
        if x
    )
    return Resultado(
        segurar=True,
        conferencia={
            **base,
            "resultado": "nao_bate",
            "motivo_sugerido": veredito.motivo_sugerido,
            "condicao_sugerida": veredito.condicao_sugerida,
            "explicacao": veredito.explicacao,
        },
        evento=(
            "A IA segurou a contestação antes de mandar: o motivo não bate com o que foi lançado. "
            f"{veredito.explicacao}"
            + (f" Sugestão: {sugestao}." if sugestao else "")
            + " Decidir na janela da devolução (Devoluções)."
        ),
    )
