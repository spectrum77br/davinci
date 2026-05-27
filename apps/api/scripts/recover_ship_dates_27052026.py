"""One-off: recupera dataSaida real do Bling pros 539 pedidos
carimbados em 27/05 cedo (movidos pra 26/05 antes). Atualiza
em_andamento_data pra a data real reportada pelo Bling.

Escopo do batch: bling_orders com
  * em_andamento_data = '2026-05-26'
  * created_at >= '2026-05-27 08:00:00+00'
  * created_at <  '2026-05-27 08:10:00+00'

Pra cada pedido: GET /pedidos/vendas/{id} no Bling, extrai
`dataSaida` (a data de envio que o Bling registra). Se for diferente
do em_andamento_data atual, atualiza TODAS as linhas (multi-item)
desse bling_id.

Idempotente: rodar de novo é no-op (todos já estarão com a data correta).
Erros por pedido são logados mas não param a execução.
Rate limit do Bling é tratado pelo próprio BlingClient (retry em 429).

Uso:
  docker compose exec -T api python -m scripts.recover_ship_dates_27052026
"""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

from sqlalchemy import select, update

from app.db import session_scope
from app.models import BlingOrder
from app.services.marketplace_shipment_check import (
    _build_bling_client,
    _get_bling_integration,
)


CUTOFF_START = datetime(2026, 5, 27, 8, 0, 0, tzinfo=UTC)
CUTOFF_END = datetime(2026, 5, 27, 8, 10, 0, tzinfo=UTC)
CURRENT_DATE = date(2026, 5, 26)
# Segunda passada: filtro removido (era date(2026, 5, 25)). Operador
# pediu pra mover TUDO pras datas reais, incluindo os 242 pedidos com
# dataSaida 20-24/05 que ficaram em 26/05 na primeira passada.
MIN_REAL_DATE = date(2000, 1, 1)


def _parse_data_saida(value) -> date | None:
    """Bling devolve YYYY-MM-DD em string. None quando não setado."""
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


async def main() -> None:
    async with session_scope() as session:
        bling_integration = await _get_bling_integration(session)
        if bling_integration is None:
            print("ERR: nenhuma Integration de Bling encontrada — abortando")
            return

        # Distinct bling_ids do batch (item_index=0 = canônico).
        rows = (
            await session.execute(
                select(BlingOrder.bling_id)
                .where(
                    BlingOrder.em_andamento_data == CURRENT_DATE,
                    BlingOrder.item_index == 0,
                    BlingOrder.created_at >= CUTOFF_START,
                    BlingOrder.created_at < CUTOFF_END,
                    BlingOrder.bling_id.isnot(None),
                )
                .order_by(BlingOrder.bling_id)
            )
        ).all()
        bling_ids = [int(r.bling_id) for r in rows]
        print(f"📦 {len(bling_ids)} pedidos no batch — iniciando")

        client = await _build_bling_client(session, bling_integration)

        stats = {
            "fetched_ok": 0,
            "updated": 0,
            "unchanged": 0,
            "skipped_old": 0,
            "no_data_saida": 0,
            "errors": 0,
        }
        # Distribuição final pra dar visão clara do resultado.
        date_distribution: dict[str, int] = {}

        for i, bid in enumerate(bling_ids, 1):
            try:
                raw = await client.get_order(bid)
            except Exception as e:  # noqa: BLE001
                stats["errors"] += 1
                print(f"  [{i}/{len(bling_ids)}] {bid} ERR {str(e)[:100]}")
                continue

            stats["fetched_ok"] += 1
            real_date = _parse_data_saida(raw.get("dataSaida"))
            if real_date is None:
                stats["no_data_saida"] += 1
                date_distribution["(sem dataSaida)"] = (
                    date_distribution.get("(sem dataSaida)", 0) + 1
                )
                continue

            key = real_date.isoformat()
            date_distribution[key] = date_distribution.get(key, 0) + 1

            # Filtro de escopo: bug do fallback começou em 25/05. Pedidos
            # com dataSaida anterior não são desse problema — provavelmente
            # são re-processamento de pedidos antigos ou outliers. Deixa
            # em 26/05 (estado atual) intocados.
            if real_date < MIN_REAL_DATE:
                stats["skipped_old"] += 1
                continue

            if real_date == CURRENT_DATE:
                stats["unchanged"] += 1
                continue

            # Atualiza TODAS as linhas desse bling_id (multi-item).
            result = await session.execute(
                update(BlingOrder)
                .where(BlingOrder.bling_id == bid)
                .values(em_andamento_data=real_date)
            )
            stats["updated"] += result.rowcount or 0

            if i % 25 == 0:
                await session.commit()
                print(
                    f"  [{i}/{len(bling_ids)}] commit parcial — "
                    f"updated={stats['updated']} unchanged={stats['unchanged']} "
                    f"errors={stats['errors']}"
                )

        await session.commit()

        print("\n✅ Concluído")
        print(f"   fetched_ok       : {stats['fetched_ok']}")
        print(f"   updated (linhas) : {stats['updated']}")
        print(f"   unchanged (=26/05): {stats['unchanged']}")
        print(f"   skipped_old (<25/05): {stats['skipped_old']}")
        print(f"   no_data_saida    : {stats['no_data_saida']}")
        print(f"   errors           : {stats['errors']}")
        print("\n📅 Distribuição das datas reais (dataSaida):")
        for d in sorted(date_distribution.keys()):
            print(f"   {d}: {date_distribution[d]}")


if __name__ == "__main__":
    asyncio.run(main())
