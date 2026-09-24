"""Robô da Logística — comandos pro executor local (apps/executor).

Vinicius, 15/09/2026: "Suspender entrega" no Envio próprio da Amazon. O
Melhor Envio não expõe isso na API (só cancela etiqueta antes da postagem e
cria reversa); a suspensão de um envio JÁ POSTADO é feita no painel deles:
Meus envios › Envios postados › Ações do envio › Suspender entrega ›
Solicitar. Regras do próprio Melhor Envio: quanto antes, maior a chance; o
frete não volta; não dá pra desfazer; a confirmação aparece no rastreio como
"Solicitação de suspensão de entrega recebida".

Quem clica é o executor (o mesmo braço local que opera a Shopee via AdsPower,
com um perfil logado no Melhor Envio). Aqui só a FILA:

  operador clica → `solicitar_suspensao` grava o comando `pending` e marca a
  linha como `pendente` → o executor puxa (`lease`, vira `claimed`) → clica →
  devolve (`registrar_resultado`): done = `solicitada`, failed = `falhou`.

O comando leva `commit=True` (o clique do operador É a decisão); a trava
final é do executor (`MELHORENVIO_CALIBRATED`), que sem ela roda em modo
seco e devolve o que encontrou na tela, pra calibrar os seletores.

Vinicius, 24/09/2026: o Melhor Envio sai do executor do Eduardo (quase sempre
dormindo) e vai pra um executor só dele no Mac Santiago. Cada executor diz no
lease o que faz (`acoes`); a suspensão é EXCLUSIVA — só vai pra quem pediu por
ela, então o executor antigo (que não manda `acoes`) para de pegá-la sem
ninguém mexer nele. O resto da fila (a leitura do Tuta) segue como estava.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Logistica, LogisticaRoboComando
from app.models.marketing import AGENTE_LOGISTICA_PREFIXO, MarketingAgentHeartbeat
from app.services import logistica_amazon_canal, logistica_track, tuta_devolucoes

logger = structlog.get_logger()

ACAO_SUSPENDER = "melhorenvio_suspender"

STATUS_PENDENTE = "pendente"
STATUS_SOLICITADA = "solicitada"
STATUS_FALHOU = "falhou"

# Comando preso em `claimed` (executor caiu no meio) volta pra fila depois disto.
LEASE_STALE = timedelta(minutes=30)

# Só vão pra executor que as pediu pelo nome no lease (ver docstring).
ACOES_EXCLUSIVAS = frozenset({ACAO_SUSPENDER})


class RoboError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def pode_suspender(row: Logistica) -> str | None:
    """None quando dá pra pedir; senão o código do motivo."""
    if (row.amazon_canal or "") != logistica_amazon_canal.CANAL_PROPRIO:
        return "logistica_nao_envio_proprio"
    if not logistica_track.is_correios(row.rastreio):
        return "logistica_sem_rastreio_correios"
    if row.entregue_em is not None:
        return "logistica_ja_entregue"
    if row.suspensao_status in (STATUS_PENDENTE, STATUS_SOLICITADA):
        return "logistica_suspensao_ja_pedida"
    return None


async def solicitar_suspensao(
    session: AsyncSession, row: Logistica, *, user_id: UUID | None
) -> LogisticaRoboComando:
    motivo = pode_suspender(row)
    if motivo:
        raise RoboError(motivo)
    cmd = LogisticaRoboComando(
        logistica_id=row.id,
        acao=ACAO_SUSPENDER,
        payload={
            "rastreio": (row.rastreio or "").strip().upper(),
            "pedido_bling": row.pedido_bling,
            "pedido_amazon": row.pedido_marketplace,
            "conta": row.conta,
            "commit": True,
        },
        created_by=user_id,
    )
    session.add(cmd)
    row.suspensao_status = STATUS_PENDENTE
    row.suspensao_em = datetime.now(UTC)
    row.suspensao_detalhe = None
    await session.commit()
    logger.info(
        "logistica_robo_suspensao_enfileirada",
        pedido=row.pedido_bling,
        rastreio=row.rastreio,
        comando=str(cmd.id),
    )
    return cmd


async def lease(
    session: AsyncSession, *, limit: int = 5, acoes: list[str] | None = None
) -> list[dict[str, Any]]:
    """Entrega ao executor os comandos pendentes (e os presos em `claimed` há
    mais de LEASE_STALE), marcando-os `claimed`. FOR UPDATE SKIP LOCKED: dois
    executores nunca pegam o mesmo. `acoes` = só essas; None (executor antigo)
    = tudo menos ACOES_EXCLUSIVAS."""
    limite_stale = datetime.now(UTC) - LEASE_STALE
    da_maquina = (
        LogisticaRoboComando.acao.in_(acoes)
        if acoes is not None
        else LogisticaRoboComando.acao.not_in(ACOES_EXCLUSIVAS)
    )
    rows = (
        await session.execute(
            select(LogisticaRoboComando)
            .where(
                da_maquina,
                or_(
                    LogisticaRoboComando.status == "pending",
                    (LogisticaRoboComando.status == "claimed")
                    & (LogisticaRoboComando.claimed_at < limite_stale),
                ),
            )
            .order_by(LogisticaRoboComando.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    agora = datetime.now(UTC)
    out: list[dict[str, Any]] = []
    for cmd in rows:
        cmd.status = "claimed"
        cmd.claimed_at = agora
        cmd.attempts = (cmd.attempts or 0) + 1
        out.append(
            {
                "id": str(cmd.id),
                "logistica_id": str(cmd.logistica_id),
                "acao": cmd.acao,
                "payload": cmd.payload or {},
                "attempts": cmd.attempts,
            }
        )
    if rows:
        await session.commit()
    logger.info("logistica_robo_lease", leased=len(out), acoes=acoes)
    return out


async def registrar_heartbeat(
    session: AsyncSession,
    *,
    agent_name: str,
    version: str | None,
    adspower_ok: bool | None,
    info: dict[str, Any],
) -> None:
    """Carimba o sinal de vida do executor do Melhor Envio (uma linha por nome,
    na tabela do executor da Shopee com o prefixo — ver AGENTE_LOGISTICA_PREFIXO)."""
    nome = f"{AGENTE_LOGISTICA_PREFIXO}{agent_name}"
    hb = (
        await session.execute(
            select(MarketingAgentHeartbeat).where(MarketingAgentHeartbeat.agent_name == nome)
        )
    ).scalar_one_or_none()
    if hb is None:
        hb = MarketingAgentHeartbeat(agent_name=nome)
        session.add(hb)
    dados = dict(info or {})
    if version:
        dados.setdefault("version", version)
    hb.last_seen_at = datetime.now(UTC)
    hb.adspower_ok = adspower_ok
    hb.info = dados
    await session.commit()


async def ultimo_heartbeat(session: AsyncSession) -> MarketingAgentHeartbeat | None:
    """O executor do Melhor Envio que deu sinal por último."""
    return (
        await session.execute(
            select(MarketingAgentHeartbeat)
            .where(MarketingAgentHeartbeat.agent_name.startswith(AGENTE_LOGISTICA_PREFIXO))
            # DESC no Postgres põe NULL na frente.
            .order_by(MarketingAgentHeartbeat.last_seen_at.desc().nulls_last())
            .limit(1)
        )
    ).scalar_one_or_none()


async def registrar_resultado(
    session: AsyncSession, comando_id: UUID, *, status: str, result: str | None
) -> LogisticaRoboComando:
    cmd = await session.get(LogisticaRoboComando, comando_id)
    if cmd is None:
        raise RoboError("comando_nao_encontrado")
    ok = status == "done"
    cmd.status = "done" if ok else "failed"
    cmd.result = (result or "")[:2000] or None
    cmd.completed_at = datetime.now(UTC)
    # Comando que não é de um pedido (ex.: leitura do Tuta) vem sem vínculo.
    row = await session.get(Logistica, cmd.logistica_id) if cmd.logistica_id else None
    if cmd.acao == tuta_devolucoes.ACAO:
        await session.commit()
        await tuta_devolucoes.entregar_resultado(session, cmd.result)
        logger.info("logistica_robo_resultado", comando=str(comando_id), status=cmd.status)
        return cmd
    if row is not None and cmd.acao == ACAO_SUSPENDER:
        row.suspensao_status = STATUS_SOLICITADA if ok else STATUS_FALHOU
        row.suspensao_detalhe = cmd.result
    await session.commit()
    logger.info(
        "logistica_robo_resultado",
        comando=str(comando_id),
        status=cmd.status,
        pedido=row.pedido_bling if row else None,
    )
    return cmd
