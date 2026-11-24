"""Histórico diário do gasto de Ads por conta (M-22, 24/11/2026, migration 0507).

A coluna Investimento da aba Faturamento soma o gasto da conta no PERÍODO, e o
`marketing_accounts.spend_today` é sobrescrito a cada coleta. Toda coleta que
escreve o `spend_today` grava também aqui o gasto de cada dia que trouxe (upsert
por conta + dia): o ML os 7 dias da janela (o mesmo número da linha diária do
`marketing_metrics`) e a Shopee os dias da resposta. O backfill do ML
(scripts/ml_ads_backfill.py --apply, até 90 dias) grava os dias que regrava.
A Amazon não entra: a coleta dela soma a janela inteira num número só (não é
o gasto de um dia).

Afiliados: não há coleta. `gasto_afiliados` fica NULL e a gravação de Ads
nunca mexe nela.

A gravação é um extra da coleta: roda num SAVEPOINT e, se falhar (ex.: código
novo no ar antes da migration — o deploy não roda migration), só registra no
log; o resto da coleta (gasto de hoje, campanhas, ACOS) segue.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

import structlog
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.marketing import MarketingGastoDia

logger = structlog.get_logger()

ORIGEM_ML = "ml_ads"
ORIGEM_SHOPEE = "shopee_ads"
# A cópia que a migration 0507 fez das linhas diárias que já existiam.
ORIGEM_COPIA_0507 = "marketing_metrics"

_CENTAVO = Decimal("0.01")


def _reais(valor: float | Decimal | None) -> Decimal | None:
    """R$ com 2 casas; None, NaN e negativo = sem dado (a CHECK recusaria o
    negativo e derrubaria a gravação dos outros dias)."""
    if valor is None:
        return None
    try:
        d = Decimal(str(valor)).quantize(_CENTAVO, rounding=ROUND_HALF_UP)
    except (ArithmeticError, ValueError):
        return None
    if not d.is_finite() or d < 0:
        return None
    return d


async def gravar_gasto_dos_dias(
    session: AsyncSession,
    *,
    account_id: UUID,
    dias: Iterable[tuple[date, float | Decimal | None]],
    origem: str,
) -> int:
    """Grava (cria ou atualiza) o gasto de Ads de cada dia da conta. Dia sem
    valor (None/negativo) não é gravado. Devolve quantos dias foram gravados
    (0 também quando a gravação falhou — fica no log, a coleta segue)."""
    linhas: dict[date, Decimal] = {}
    for dia, valor in dias:
        reais = _reais(valor)
        if reais is not None:
            linhas[dia] = reais
    if not linhas:
        return 0
    stmt = pg_insert(MarketingGastoDia).values(
        [
            {"marketing_account_id": account_id, "dia": dia, "gasto_ads": reais, "origem": origem}
            for dia, reais in sorted(linhas.items())
        ]
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[MarketingGastoDia.marketing_account_id, MarketingGastoDia.dia],
        set_={
            "gasto_ads": stmt.excluded.gasto_ads,
            "origem": stmt.excluded.origem,
            "atualizado_em": func.now(),
        },
    )
    try:
        async with session.begin_nested():
            await session.execute(stmt)
    except SQLAlchemyError as e:
        logger.warning(
            "marketing_gasto_dia_nao_gravado",
            account_id=str(account_id), origem=origem, dias=len(linhas), erro=str(e)[:300],
        )
        return 0
    return len(linhas)
