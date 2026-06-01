"""One-off: limpa histórico inflado de marketing_metrics.revenue.

Antes do fix dos sync (ml/amazon escreviam `bling.total` da janela em vez de
`bling.by_day[today]`), o row do dia recebia o somatório dos últimos N dias.
Quando agregado por 7d/30d isso somava-se de novo → faturamento ~2x inflado.

Este script re-grava `marketing_metrics.revenue` por (account, dia) usando
`get_bling_revenue().by_day` (fonte autoritativa). Cobre ml + amazon (shopee
já estava certo). Dry-run por padrão; --apply pra gravar.

Uso:
  docker compose -f docker-compose.yml -f docker-compose.prod.yml \\
    exec -T api python -m scripts.backfill_marketing_revenue --days=90
  docker compose ... python -m scripts.backfill_marketing_revenue --days=90 --apply
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import cast as sa_cast
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import DATE

from app.db import session_scope
from app.models import Integration, MarketingAccount, MarketingMetric
from app.services.marketing.bling_revenue import get_bling_revenue

_PLATAFORMAS = ("ml", "amazon")


async def main(days: int, apply_flag: bool) -> None:
    today = datetime.now(UTC).date()
    start = today - timedelta(days=days)
    print(f"{'APPLY' if apply_flag else 'DRY-RUN'} backfill marketing_metrics.revenue "
          f"de {start} a {today} (plataformas: {_PLATAFORMAS})")

    async with session_scope() as session:
        accounts = (await session.execute(
            select(MarketingAccount).where(
                MarketingAccount.platform.in_(_PLATAFORMAS),
                MarketingAccount.integration_id.isnot(None),
            )
        )).scalars().all()
        print(f"  contas elegíveis: {len(accounts)}")

        stats = {"accounts": 0, "rows_updated": 0, "rows_unchanged": 0, "no_revenue": 0}

        for acc in accounts:
            integ = await session.get(Integration, acc.integration_id)
            if integ is None:
                print(f"  skip {acc.platform}:{acc.name} — integration ausente")
                continue
            bling = await get_bling_revenue(session, integ, start=start, end=today)
            if bling is None or not bling.by_day:
                stats["no_revenue"] += 1
                continue
            stats["accounts"] += 1

            for day, rev in bling.by_day.items():
                # Match rows do account no dia (timestamp::date == day).
                day_stmt = (
                    select(MarketingMetric)
                    .where(
                        MarketingMetric.account_id == acc.id,
                        sa_cast(MarketingMetric.timestamp, DATE) == day,
                    )
                )
                metrics = (await session.execute(day_stmt)).scalars().all()
                for m in metrics:
                    new_val = round(float(rev), 2)
                    if abs(float(m.revenue) - new_val) < 0.005:
                        stats["rows_unchanged"] += 1
                        continue
                    if apply_flag:
                        await session.execute(
                            update(MarketingMetric)
                            .where(MarketingMetric.id == m.id)
                            .values(revenue=new_val)
                        )
                    stats["rows_updated"] += 1
                    if not apply_flag:
                        print(
                            f"  {acc.platform}:{acc.name} {day} "
                            f"{m.revenue:.2f} → {new_val:.2f}"
                        )

        if apply_flag:
            await session.commit()
        else:
            await session.rollback()

    print("\nResumo:")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    if not apply_flag:
        print("\nDRY-RUN — passe --apply pra gravar.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=90,
                   help="Janela em dias retroativos (default: 90)")
    p.add_argument("--apply", action="store_true",
                   help="Aplica as mudanças (sem isso é dry-run)")
    args = p.parse_args()
    asyncio.run(main(days=args.days, apply_flag=args.apply))
