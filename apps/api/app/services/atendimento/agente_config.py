"""Quando o AGENTE (Claude) responde num canal — decisão 2 do dono, 20/10/2026.

Três coisas juntas, senão é o legado (Groq, prompt v10) exatamente como está:

  1. a chave Claude (`chave_do_agente`): `ATENDIMENTO_AGENTE_API_KEY` ou, se
     ela não for `sk-ant-`, a `ATENDIMENTO_LLM_API_KEY` quando for `sk-ant-`.
     Nunca a chave do Groq (`gsk_...`): mandar a chave de um provedor para o
     outro vaza segredo e dá 401 em toda conversa;
  2. o interruptor geral `atendimento_agente_ativo` (nasce ligado: sem chave
     nada roda);
  3. o canal com `atendimento_canais.agente = true` (o botão "Agente
     (Claude)" da tela de canais, só admin).

Um lugar só para essa regra: o `_gerar` da IA, a Caixa Humano, o envio e a
tela leem daqui — se cada um decidisse, o rascunho sairia do agente e a
triagem seguiria a régua do legado.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

PREFIXO_CHAVE_CLAUDE = "sk-ant-"


def _texto(valor: Any) -> str:
    # `SecretStr` também serve (se a chave um dia mudar de tipo).
    segredo = getattr(valor, "get_secret_value", None)
    return str(segredo() if callable(segredo) else valor or "").strip()


def chave_do_agente(settings: Any) -> str | None:
    """A chave Claude que o agente usa, ou None (sem chave = o agente não roda)."""
    for nome in ("atendimento_agente_api_key", "atendimento_llm_api_key"):
        chave = _texto(getattr(settings, nome, ""))
        if chave.startswith(PREFIXO_CHAVE_CLAUDE):
            return chave
    return None


def agente_ligado(settings: Any, canal: Any) -> bool:
    """O agente responde NESTE canal? (interruptor geral E chave Claude E botão do canal)

    `canal` = `AtendimentoCanal` (ou um dicionário com `agente`); None — a
    conversa sem canal — é o legado.
    """
    if canal is None or not getattr(settings, "atendimento_agente_ativo", False):
        return False
    marcado = canal.get("agente") if isinstance(canal, Mapping) else getattr(canal, "agente", None)
    return marcado is True and chave_do_agente(settings) is not None


def plataformas_do_replay(settings: Any) -> frozenset[str]:
    """As plataformas do replay noturno ("shopee,tiktok" → {"shopee", "tiktok"})."""
    bruto = str(getattr(settings, "atendimento_agente_replay_plataformas", "") or "")
    return frozenset(p.strip().lower() for p in bruto.split(",") if p.strip())
