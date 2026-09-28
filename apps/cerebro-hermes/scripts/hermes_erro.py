"""Erro do Hermes em português, pro que vai gravado no chamado.

Vinicius, 28/09/2026 (297335): "ta tudo em inglês, uma grande porcaria". A
assinatura do ChatGPT bateu no limite de uso e o texto cru do Hermes ("ChatGPT
or Codex Subscription rate-limited… Send /retry… hermes fallback add…") virou a
decisão da IA de Chamado. Só vale quando a IA NÃO devolveu o formato combinado
(ACAO/RESUMO, ESCOLHA): com ele, quem fala é ela.
"""

from __future__ import annotations

import re

_LIMITE = re.compile(r"rate.?limit|usage limit|HTTP 429|too many requests", re.I)
_PROVEDOR = re.compile(
    r"Provider said:|Send /retry|switch models with /model|API call failed|"
    r"Unauthorized|invalid api key|HTTP [45]\d\d",
    re.I,
)


def falha(saida: str | None) -> str | None:
    """Frase em português se a saída do Hermes é um erro dele (não da IA)."""
    t = saida or ""
    if _LIMITE.search(t):
        m = re.search(r"resets at (\d{1,2}:\d{2})", t, re.I)
        h = re.search(r"resets in ~?\s*(\d+)\s*h", t, re.I)
        volta = (
            f" A cota volta às {m.group(1)}."
            if m
            else f" A cota volta em cerca de {h.group(1)} h."
            if h
            else ""
        )
        return (
            "a IA de Chamado ficou sem cota no ChatGPT (limite de uso da assinatura) e "
            f"não fez nada na loja.{volta} Depois disso, mande a instrução de novo."
        )
    if _PROVEDOR.search(t):
        m = re.search(r"HTTP (\d{3})", t)
        cod = f" (erro {m.group(1)})" if m else ""
        return (
            f"a IA de Chamado não conseguiu falar com o ChatGPT{cod} e não fez nada na "
            "loja. Mande a instrução de novo; se repetir, alguém precisa olhar a IA no "
            "Mac Santiago."
        )
    return None
