"""Dia fechado que o robô precisa reabrir (16/10, revisão).

Quem fecha um dia confere linha por linha e o saldo. Se depois disso uma linha nova
entra naquele dia (repasse recuperado, relatório atrasado do Mercado Pago, janela de
releitura da Amazon…), o "fechado" deixa de valer: o dia volta a aberto e o robô fecha
de novo sozinho se continuar 100% casado — senão ele aparece no Resumo pra alguém olhar.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConciliacaoLoja
from app.models.conciliacao import ETAPA_PELO_ROBO, ConciliacaoDia

logger = structlog.get_logger()


async def reabrir_dias(
    session: AsyncSession,
    loja: ConciliacaoLoja,
    dias: Iterable[date | None],
    *,
    motivo: str,
    por: str = ETAPA_PELO_ROBO,
) -> list[str]:
    """Reabre os dias fechados da lista; devolve os que estavam fechados (ISO)."""
    alvo = sorted({d for d in dias if d})
    if not alvo:
        return []
    rows = (
        (
            await session.execute(
                select(ConciliacaoDia).where(
                    ConciliacaoDia.integration_id == loja.integration_id,
                    ConciliacaoDia.dia.in_(alvo),
                    ConciliacaoDia.fechado_em.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    reabertos: list[str] = []
    agora = datetime.now(UTC)
    for r in rows:
        r.fechado_em = None
        r.fechado_por = None
        r.fechado_motivo = None
        r.reaberto_em = agora
        r.reaberto_por = por[:120]
        r.reaberto_motivo = motivo
        reabertos.append(r.dia.isoformat())
    if reabertos:
        await session.flush()
        logger.info(
            "conciliacao_dia_reaberto_pelo_robo", loja=loja.conta, dias=reabertos, motivo=motivo
        )
    return reabertos
