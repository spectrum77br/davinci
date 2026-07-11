"""Oferta Relâmpago (Shopee flash-sale) — enqueue diário da duplicação.

Mesma filosofia do reconciler (`reconcile.py`): NÃO chama a Shopee, só escreve
no outbox `marketing_commands`. O cron `marketing_flash_duplicate` roda uma vez
por dia (01:00 BRT) e chama `enqueue_flash_duplicates`, que enfileira UM comando
`flash_duplicate` (executor='browser', payload {"commit": true}) por conta
`flash_duplicate_enabled`. O executor LOCAL (marionete/AdsPower) puxa via
/agent/lease e duplica a oferta 'Em andamento' pro próximo dia com aberturas.

Segurança em duas camadas:
  1. `commit=true` só CRIA de verdade se o executor tiver SELECTORS_CALIBRATED=true
     no .env — senão o flashsale.ts recusa o Confirmar final (nada é criado).
  2. Dedup por ação: se já existe um `flash_duplicate` pending/claimed pra conta
     (ex.: um "Duplicar agora" manual que o operador acabou de clicar, ou uma
     re-execução do cron), não empilha um segundo.

Gated por `enable_marketing` no worker (no-op em prod até ligar o módulo).
"""
from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.marketing import MarketingAccount, MarketingCommand

logger = structlog.get_logger()


async def _has_open_flash_command(session: AsyncSession, account_id) -> bool:
    """True se já há um flash_duplicate pending/claimed pra conta — trabalho em
    voo que não devemos duplicar. Dedup específico da ação (diferente do
    reconciler, que dedupa qualquer comando aberto) pra não brigar com um
    pause/resume coincidente da agenda."""
    n = (
        await session.execute(
            select(func.count())
            .select_from(MarketingCommand)
            .where(
                MarketingCommand.account_id == account_id,
                MarketingCommand.action == "flash_duplicate",
                MarketingCommand.status.in_(("pending", "claimed")),
            )
        )
    ).scalar_one()
    return n > 0


async def enqueue_flash_duplicates(session: AsyncSession) -> dict[str, Any]:
    """Uma passada de enfileiramento. Retorna um resumo pro cron logar."""
    accounts = (
        await session.execute(
            select(MarketingAccount).where(
                MarketingAccount.platform == "shopee",
                MarketingAccount.flash_duplicate_enabled.is_(True),
            )
        )
    ).scalars().all()

    checked = enqueued = 0
    for acc in accounts:
        checked += 1
        if await _has_open_flash_command(session, acc.id):
            continue  # comando em voo já vai duplicar
        session.add(
            MarketingCommand(
                account_id=acc.id,
                campaign_external_id=None,  # conta inteira
                platform=acc.platform,
                action="flash_duplicate",
                payload={"commit": True},
                status="pending",
                source="schedule",
                executor="browser",
            )
        )
        enqueued += 1
        logger.info("marketing_flash_enqueue", account_id=str(acc.id), name=acc.name)

    await session.commit()
    return {"checked": checked, "enqueued": enqueued}
