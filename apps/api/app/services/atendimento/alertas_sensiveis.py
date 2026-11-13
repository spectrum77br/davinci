"""Os AVISOS dos assuntos sensíveis (13/11/2026).

O Eduardo: cada envio sozinho de assunto sensível (reembolso, cancelamento, troca, reclamação
forte — `tipos_pergunta.SENSIVEIS`) avisa e entra na revisão diária (a aba Banco de
respostas); e ele quer saber quando um assunto sensível passa da régua dos sensíveis (85%).

- `avisar_envios_sensiveis` (cron de 5 em 5 min): os envios da IA (origem davinci_ia) de
  rascunho sensível desde a última rodada (o cursor no Redis), um aviso no Telegram por envio,
  com o assunto, a loja e o link da conversa. Sem texto de cliente nem da resposta.
- `aviso_da_regua`: a frase do assunto sensível que a régua acabou de soltar numa loja
  (`regua.aplicar` manda, uma vez por dia por canal e assunto).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import select

from app.services.atendimento import constantes, duvida, regua, tipos_pergunta

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

CHAVE_CURSOR = "atd:alertas_sensiveis:ate"
# Na primeira rodada (sem cursor), olha só este tanto para trás.
JANELA_INICIAL = timedelta(minutes=15)
MAX_POR_RODADA = 30


def _link(conversa_id: Any) -> str:
    from app.services.atendimento import sync

    return sync._link_da_tela(conversa_id)


def frase_do_envio(assunto: str | None, loja: str | None, plataforma: str | None, link: str) -> str:
    """O aviso de um envio sozinho sensível. PURA."""
    nome = duvida.nome_do_assunto(assunto)
    onde = " · ".join(x for x in (loja, plataforma) if x)
    return (
        f"🔔 Saiu sozinho (assunto sensível): <b>{nome}</b>"
        + (f" — {onde}" if onde else "")
        + f"\nConfira na revisão do dia (aba Banco de respostas) ou na conversa: {link}"
    )


def aviso_da_regua(assunto: str, loja: str | None, aprovacao: float, decisoes: int) -> str:
    """O aviso do assunto sensível que a régua soltou numa loja. PURA."""
    nome = duvida.nome_do_assunto(assunto)
    return (
        f"✅ Régua: <b>{nome}</b> passou de {round(regua.LIMIAR_SENSIVEL * 100)}% de aprovação"
        " da equipe"
        + (f" em {loja}" if loja else "")
        + f" ({round(aprovacao * 100)}% nas últimas {decisoes} decisões). "
        "A partir de agora, esse assunto sai sozinho quando há resposta do banco que encaixe."
    )


async def envios_sensiveis(session: AsyncSession, desde: datetime, ate: datetime) -> list[Any]:
    """Os envios da IA de rascunho sensível na janela, os mais antigos primeiro."""
    from app.models import (
        AtendimentoConversa,
        AtendimentoMensagem,
        AtendimentoRascunho,
        Integration,
    )

    m, r, c = AtendimentoMensagem, AtendimentoRascunho, AtendimentoConversa
    return list(
        (
            await session.execute(
                select(
                    m.id,
                    m.enviada_em,
                    r.categoria,
                    c.id.label("conversa_id"),
                    c.plataforma,
                    Integration.name.label("loja"),
                )
                .join(r, r.id == m.rascunho_id)
                .join(c, c.id == m.conversa_id)
                .outerjoin(Integration, Integration.id == c.integration_id)
                .where(
                    m.origem == constantes.ORIGEM_IA,
                    m.enviada_em > desde,
                    m.enviada_em <= ate,
                    r.conversa_id == m.conversa_id,
                    r.categoria.in_(tuple(tipos_pergunta.SENSIVEIS)),
                )
                .order_by(m.enviada_em)
                .limit(MAX_POR_RODADA)
            )
        ).all()
    )


async def avisar_envios_sensiveis(session: AsyncSession, *, agora: datetime | None = None) -> int:
    """Uma rodada: avisa cada envio sensível desde o cursor e anda o cursor. Devolve quantos."""
    from app.redis_client import redis
    from app.services.atendimento.agente import custo

    agora = agora or datetime.now(UTC)
    try:
        bruto = await redis.get(CHAVE_CURSOR)
    except Exception:  # noqa: BLE001 — sem Redis, sem aviso (a revisão diária mostra igual)
        return 0
    try:
        desde = datetime.fromisoformat(bruto.decode() if isinstance(bruto, bytes) else bruto)
    except (TypeError, ValueError, AttributeError):
        desde = agora - JANELA_INICIAL
    linhas = await envios_sensiveis(session, desde, agora)
    for linha in linhas:
        await custo.avisar_telegram(
            frase_do_envio(linha.categoria, linha.loja, linha.plataforma, _link(linha.conversa_id))
        )
    # O cursor anda até o último avisado (se a rodada encheu) ou até agora.
    ate = linhas[-1].enviada_em if len(linhas) >= MAX_POR_RODADA else agora
    try:
        await redis.set(CHAVE_CURSOR, ate.isoformat(), ex=30 * 24 * 3600)
    except Exception:  # noqa: BLE001
        logger.warning("atendimento_alertas_sensiveis_cursor_falhou")
    return len(linhas)
