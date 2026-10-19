"""Consultas de estoque superadas por uma compra confirmada na conversa.

O comprador precisa dizer que comprou e a loja precisa ter enviado o card
estruturado desse pedido. Somente planejamento de estoque anterior perde
prioridade; dúvidas sobre produto, direitos e documentos continuam pendentes.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from app.services.atendimento.validador import plano_de

_COMPRA = re.compile(
    r"^(?:(?:oi|ola|bom dia|boa tarde|boa noite)[!,.: ]*)?"
    r"(?:ja\s+)?(?:realizei\s+(?:o\s+)?pedido|fiz\s+(?:o\s+)?pedido|"
    r"comprei|finalizei\s+(?:a\s+)?compra|efetuei\s+(?:a\s+)?compra)\b"
)
_OUTRA_DEMANDA = re.compile(
    r"\b(?:garant\w*|defeit\w*|cancel\w*|devol\w*|troc\w*|reembols\w*|"
    r"estorn\w*|reclam\w*|procon|justic\w*|not[ao]\s+fiscal|nfe?|danfe|"
    r"original\w*|lacrad\w*|usad\w*|compat\w*|pulseira\w*|medid\w*|"
    r"envio|enviam|enviar|entreg\w*|prazo\w*|frete\w*|foto\w*|video\w*|"
    r"endereco|instruc\w*|prompt|sistema|atendente|humano|pessoa|pix|boleto)\b"
)
_ESTOQUE = re.compile(r"\b(?:estoque|disponiveis|disponivel)\b")
_QUANTIDADE = re.compile(r"\b(?:quantos|quantas|quantidade|unidades)\b")
_PLANEJAMENTO = re.compile(
    r"\b(?:planejar|planejando|conseguindo\s+o\s+dinheiro|juntando\s+(?:o\s+)?dinheiro)\b"
)
_FIM_ESTOQUE = re.compile(r"\b(?:acaba|acabar|esgota|esgotar)\b")
_PALAVRAS_ESTOQUE = frozenset(
    "quantos quantas qual a quantidade de unidades unidade desse deste desse modelo "
    "desses destes do da dos das o os as em no estoque disponivel disponiveis voces "
    "voce vc vcs ainda tem possuem existe existem restam restante restantes sobrando "
    "apple watch series serie cinza preto preta branco branca rosa azul verde dourado "
    "prata vermelho vermelha cor cores".split()
)
_PALAVRAS_PLANEJAMENTO = frozenset(
    "so para eu me planejar planejando quando voce voces vc vcs acha acham que acaba "
    "acabar esgota esgotar estoque o os a as do da desse deste ainda estou conseguindo "
    "juntando dinheiro em".split()
)


def _momento(m: Any) -> datetime | None:
    quando = getattr(m, "enviada_em", None) or getattr(m, "created_at", None)
    if not isinstance(quando, datetime):
        return None
    return quando.replace(tzinfo=UTC) if quando.tzinfo is None else quando.astimezone(UTC)


def _card_de_compra(m: Any, compra: Any, pedido_marketplace: str) -> bool:
    payload = getattr(m, "payload", None)
    if not isinstance(payload, dict) or getattr(m, "autor", None) != "loja":
        return False
    conteudo = payload.get("content")
    if (
        payload.get("source") != "openapi"
        or payload.get("message_type") != "order"
        or not isinstance(conteudo, dict)
        or not re.fullmatch(r"\d{6}[A-Za-z0-9]{8}", str(conteudo.get("order_sn") or ""))
        or str(conteudo.get("order_sn") or "") != pedido_marketplace
        or getattr(m, "status", None) == "falhou"
    ):
        return False
    card_em, compra_em = _momento(m), _momento(compra)
    return bool(card_em and compra_em and compra_em <= card_em <= compra_em + timedelta(days=1))


def _so_planejamento(texto: str) -> bool:
    plano = plano_de(texto)
    if _OUTRA_DEMANDA.search(plano):
        return False
    # Não descartar uma segunda pergunta só porque a primeira cita estoque.
    partes = [p.strip(" ,.") for p in re.split(r"[?!;\n]+", plano) if p.strip(" ,.")]

    def coberta(parte: str) -> bool:
        palavras = set(re.findall(r"[a-z]+", parte))
        return bool(
            (_ESTOQUE.search(parte) and _QUANTIDADE.search(parte) and palavras <= _PALAVRAS_ESTOQUE)
            or (
                _PLANEJAMENTO.search(parte)
                and _FIM_ESTOQUE.search(parte)
                and palavras <= _PALAVRAS_PLANEJAMENTO
            )
            or parte in ("estou ainda conseguindo o dinheiro", "estou conseguindo o dinheiro")
        )

    return bool(partes) and all(coberta(p) for p in partes)


def depois_da_compra(perguntas: list[Any], mensagens: Sequence[Any]) -> list[Any]:
    """Preserva tudo, salvo consulta prévia de estoque explicitamente superada."""
    pedido_marketplace = getattr(mensagens, "pedido_marketplace", None)
    if not pedido_marketplace or getattr(mensagens, "turno_incompleto", False):
        return perguntas
    compra = next(
        (
            m
            for m in reversed(perguntas)
            if _COMPRA.search(plano_de(getattr(m, "texto", None) or ""))
            and any(_card_de_compra(card, m, pedido_marketplace) for card in mensagens)
        ),
        None,
    )
    if compra is None:
        return perguntas
    compra_em = _momento(compra)
    return [
        m
        for m in perguntas
        if not (
            (em := _momento(m)) is not None
            and compra_em is not None
            and em < compra_em
            and _so_planejamento(getattr(m, "texto", None) or "")
        )
    ]
