"""One-off: recupera ship date real dos pedidos do batch de 27/05
chamando o **marketplace** (não o Bling).

Por que: Bling `dataSaida` mostrou-se não-confiável (parece ser a data
planejada/criação, não a real). Marketplaces sim registram o evento de
SHIPPED com timestamp.

Estratégia por marketplace:
  * ML        → get_shipment(shipping_id).status_history.date_shipped
                (sinal direto e confiável; convertido pra BRT)
  * Shopee    → get_order_status_map(order_sn).update_time (epoch UTC;
                aproximação — para pedidos já entregues pode refletir
                hora da entrega em vez do despacho)
  * Amazon    → get_order_status(numeroloja).last_update_date (mesma
                limitação do Shopee)
  * Outros    → skip (mantém em_andamento_data atual)

Escopo: bling_orders com
  * em_andamento_data = '2026-05-26'
  * created_at na janela 27/05 08:00-08:10 UTC
  * situacao = '15'
  * item_index = 0  (canônico — atualizamos todas as linhas do bling_id)

Idempotente: rodar de novo é seguro (re-fetch + update se valor mudar).
Erros por pedido logados mas não param a execução.

Uso:
  docker compose exec -T api python -m scripts.recover_ship_dates_marketplace
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select, update

from app.db import session_scope
from app.models import BlingOrder, Integration
from app.models.company import Store
from app.models.enums import IntegrationPlatform
from app.security.cipher import decrypt_json
from app.services.marketplaces.amazon import AmazonClient
from app.services.marketplaces.ml import MercadoLivreClient
from app.services.marketplaces.shopee import ShopeeClient


CUTOFF_START = datetime(2026, 5, 27, 8, 0, 0, tzinfo=UTC)
CUTOFF_END = datetime(2026, 5, 27, 8, 10, 0, tzinfo=UTC)
CURRENT_DATE = date(2026, 5, 26)
_BRT = ZoneInfo("America/Sao_Paulo")


def _parse_iso_to_brt_date(value) -> date | None:
    if not value:
        return None
    try:
        s = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(_BRT).date()
    except (TypeError, ValueError):
        return None


def _epoch_to_brt_date(epoch_seconds) -> date | None:
    if not epoch_seconds:
        return None
    try:
        return datetime.fromtimestamp(int(epoch_seconds), tz=UTC).astimezone(_BRT).date()
    except (TypeError, ValueError):
        return None


async def _ship_date_ml(client: MercadoLivreClient, order: BlingOrder) -> date | None:
    """ML: status_history.date_shipped do shipment é o evento real de envio."""
    try:
        data = await client.get_order(str(order.numeroloja))
    except Exception:  # noqa: BLE001
        return None
    shipping_id = (data.get("shipping") or {}).get("id")
    if not shipping_id:
        # Sem shipping (raro em malas) — usa date_closed como fallback.
        return _parse_iso_to_brt_date(data.get("date_closed"))
    try:
        ship = await client.get_shipment(str(shipping_id))
    except Exception:  # noqa: BLE001
        return None
    sh = (ship.get("status_history") or {}) if isinstance(ship.get("status_history"), dict) else {}
    return (
        _parse_iso_to_brt_date(sh.get("date_shipped"))
        or _parse_iso_to_brt_date(ship.get("last_updated"))
    )


async def _ship_dates_shopee(
    client: ShopeeClient, orders: list[BlingOrder],
) -> dict[int, date]:
    """Shopee: update_time (last status change). Em batch de até 50."""
    order_sns = [str(o.numeroloja) for o in orders if o.numeroloja and o.bling_id]
    if not order_sns:
        return {}
    status_map = await client.get_order_status_map(order_sns)
    out: dict[int, date] = {}
    for o in orders:
        info = status_map.get(str(o.numeroloja))
        if not info:
            continue
        d = _epoch_to_brt_date(info.get("update_time"))
        if d:
            out[int(o.bling_id)] = d
    return out


async def _ship_date_amazon(client: AmazonClient, order: BlingOrder) -> date | None:
    """Amazon: last_update_date do get_order_status."""
    try:
        result = await client.get_order_status(str(order.numeroloja))
    except Exception:  # noqa: BLE001
        return None
    if not result:
        return None
    return _parse_iso_to_brt_date(result.get("last_update_date"))


async def _build_client(
    session, integration: Integration,
):
    """Decrypta creds, monta o cliente certo e configura on_token_refresh."""
    creds = decrypt_json(integration.credentials)

    async def _persist(new_creds: dict) -> None:
        from app.security.cipher import encrypt_json

        integration.credentials = encrypt_json(new_creds)
        exp = new_creds.get("expires_at")
        if exp:
            integration.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        await session.flush()

    p = integration.platform
    if p == IntegrationPlatform.ML:
        return MercadoLivreClient(creds, on_token_refresh=_persist)
    if p == IntegrationPlatform.SHOPEE:
        return ShopeeClient(creds, on_token_refresh=_persist)
    if p == IntegrationPlatform.AMAZON:
        return AmazonClient(creds, on_token_refresh=_persist)
    return None


async def main() -> None:
    async with session_scope() as session:
        # Pedidos do batch (item_index=0 = canônico, atualizamos todas
        # linhas do bling_id depois).
        orders = (await session.execute(
            select(BlingOrder)
            .where(
                BlingOrder.em_andamento_data == CURRENT_DATE,
                BlingOrder.item_index == 0,
                BlingOrder.created_at >= CUTOFF_START,
                BlingOrder.created_at < CUTOFF_END,
                BlingOrder.situacao == "15",
                BlingOrder.bling_id.isnot(None),
                BlingOrder.loja.isnot(None),
            )
        )).scalars().all()
        print(f"📦 {len(orders)} pedidos no batch")

        # Mapeia loja → integration (uma query por loja).
        by_loja: dict[str, list[BlingOrder]] = defaultdict(list)
        for o in orders:
            by_loja[str(o.loja)].append(o)

        store_rows = (await session.execute(
            select(Store.bling_store_id, Integration)
            .join(Integration, Integration.id == Store.integration_id)
            .where(Store.bling_store_id.in_([int(k) for k in by_loja.keys() if k.isdigit()]))
        )).all()
        integration_by_loja: dict[str, Integration] = {}
        for r in store_rows:
            integration_by_loja[str(r.bling_store_id)] = r[1]

        stats = {
            "ml_lookups": 0, "shopee_lookups": 0, "amazon_lookups": 0,
            "updated": 0, "unchanged": 0, "skipped_no_integration": 0,
            "skipped_no_date": 0, "errors": 0,
        }
        date_dist: dict[str, int] = {}

        for loja, group in by_loja.items():
            integration = integration_by_loja.get(loja)
            if not integration:
                stats["skipped_no_integration"] += len(group)
                continue
            client = await _build_client(session, integration)
            if client is None:
                stats["skipped_no_integration"] += len(group)
                continue

            # Per-platform processing — shopee é batch, ML/Amazon são per-pedido.
            results: dict[int, date | None] = {}
            try:
                if integration.platform == IntegrationPlatform.SHOPEE:
                    results = {bid: d for bid, d in (await _ship_dates_shopee(client, group)).items()}
                    stats["shopee_lookups"] += len(group)
                elif integration.platform == IntegrationPlatform.ML:
                    for o in group:
                        d = await _ship_date_ml(client, o)
                        if d:
                            results[int(o.bling_id)] = d
                        stats["ml_lookups"] += 1
                elif integration.platform == IntegrationPlatform.AMAZON:
                    for o in group:
                        d = await _ship_date_amazon(client, o)
                        if d:
                            results[int(o.bling_id)] = d
                        stats["amazon_lookups"] += 1
                else:
                    stats["skipped_no_integration"] += len(group)
                    continue
            except Exception as e:  # noqa: BLE001
                print(f"  loja {loja} ({integration.platform.value}) ERR {str(e)[:120]}")
                stats["errors"] += len(group)
                continue

            # Atualiza por bling_id (afeta todas as linhas — multi-item).
            for bid, real_date in results.items():
                if real_date is None:
                    stats["skipped_no_date"] += 1
                    continue
                key = real_date.isoformat()
                date_dist[key] = date_dist.get(key, 0) + 1
                if real_date == CURRENT_DATE:
                    stats["unchanged"] += 1
                    continue
                result = await session.execute(
                    update(BlingOrder)
                    .where(BlingOrder.bling_id == bid)
                    .values(em_andamento_data=real_date)
                )
                stats["updated"] += result.rowcount or 0

            # No-result orders (sem date_shipped retornado)
            for o in group:
                if int(o.bling_id) not in results:
                    stats["skipped_no_date"] += 1

            await session.commit()
            print(f"  loja {loja} ({integration.platform.value}) {len(group)} pedidos → {len(results)} datas obtidas")

        print("\n✅ Concluído")
        for k, v in stats.items():
            print(f"   {k:<30}: {v}")
        print("\n📅 Distribuição das datas reais (do marketplace):")
        for d in sorted(date_dist.keys()):
            print(f"   {d}: {date_dist[d]}")


if __name__ == "__main__":
    asyncio.run(main())
