"""Preenche a assinatura de status da Shopee (`Logistica.meli_status`) puxando
da API v2 da Shopee.

Espelha o `logistica_meli.py`, mas a Shopee tem um vocabulário PRÓPRIO e bem
mais enxuto: o sinal de pós-venda que importa pro fluxo (cancelamento,
devolução, concluído, em envio) já vem no `order_status` do pedido — e a API v2
entrega isso em lote via `get_order_status_map`. Então a assinatura da Shopee é
um único campo:

    {"order_status": "COMPLETED" | "CANCELLED" | "TO_RETURN" | ...}

renderizado em PT por `logistica_rules.assinatura_shopee`. Camadas futuras
(rastreio, devolução detalhada) podem enriquecer, mas o order_status já casa a
maioria dos casos.

Só se aplica a pedidos Shopee. Best-effort: pedido que a Shopee não devolve fica
sem status (não derruba o lote).
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy import Text, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Integration, IntegrationPlatform, Logistica
from app.security.cipher import decrypt_json, encrypt_json
from app.services import logistica_rules
from app.services.marketplaces.shopee import ShopeeClient

logger = structlog.get_logger()

_SHOPEE_PLATAFORMAS = logistica_rules._SHOPEE_PLATAFORMAS


async def build_enrichment(client: ShopeeClient, order_sn: str) -> dict[str, dict]:
    """Puxa da Shopee o `order_status` do pedido e monta a assinatura.

    Retorna `{"meli_status": {"order_status": "..."} | {}}`. Best-effort: se a
    Shopee não devolver o pedido, `meli_status` fica vazio."""
    status_map = await client.get_order_status_map([str(order_sn)])
    info = status_map.get(str(order_sn)) or {}
    st = (info.get("status") or "").strip().upper()
    meli: dict[str, str] = {"order_status": st} if st else {}
    return {"meli_status": meli}


async def _shopee_integration_for_conta(
    session: AsyncSession, conta: str | None
) -> Integration | None:
    """Integração Shopee cuja `name` casa (trim+lower) com a `conta` da linha."""
    key = (conta or "").strip().lower()
    if not key:
        return None
    rows = (
        await session.execute(
            select(Integration).where(Integration.platform == IntegrationPlatform.SHOPEE)
        )
    ).scalars().all()
    for it in rows:
        if (it.name or "").strip().lower() == key:
            return it
    return None


def _build_shopee_client(session: AsyncSession, integration: Integration) -> ShopeeClient:
    creds = decrypt_json(integration.credentials)

    async def _persist(new_creds: dict) -> None:
        integration.credentials = encrypt_json(new_creds)
        exp = new_creds.get("expires_at")
        if exp:
            integration.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        await session.flush()

    return ShopeeClient(creds, on_token_refresh=_persist)


class ShopeeEnrichError(Exception):
    """Falha de negócio ao enriquecer uma linha Shopee (código pro endpoint)."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


async def enrich_row(
    session: AsyncSession,
    row: Logistica,
    *,
    client_cache: dict[str, ShopeeClient] | None = None,
) -> bool:
    """Preenche `row.meli_status` puxando o order_status da Shopee. Retorna True
    se atualizou.

    Levanta `ShopeeEnrichError` com código quando não dá pra prosseguir (linha
    não-Shopee, sem pedido de marketplace, conta sem integração Shopee)."""
    if (row.plataforma or "").strip().lower() not in _SHOPEE_PLATAFORMAS:
        raise ShopeeEnrichError("logistica_nao_shopee")
    order_sn = (row.pedido_marketplace or "").strip()
    if not order_sn:
        raise ShopeeEnrichError("logistica_sem_pedido")

    conta = row.conta
    client: ShopeeClient | None = None
    if client_cache is not None and conta in client_cache:
        client = client_cache[conta]
    else:
        integ = await _shopee_integration_for_conta(session, conta)
        if integ is None:
            raise ShopeeEnrichError("logistica_sem_integracao")
        client = _build_shopee_client(session, integ)
        if client_cache is not None:
            client_cache[conta] = client

    enr = await build_enrichment(client, order_sn)
    row.meli_status = enr["meli_status"]
    return True


async def enrich_recent(
    session: AsyncSession,
    *,
    limit: int = 100,
    only_empty: bool = True,
) -> dict[str, int]:
    """Enriquece um lote de linhas Shopee (mais recentes primeiro), reusando o
    client por conta. `only_empty` pula linhas que já têm meli_status."""
    stmt = select(Logistica).where(
        func.lower(func.trim(Logistica.plataforma)).in_(tuple(_SHOPEE_PLATAFORMAS))
    )
    if only_empty:
        stmt = stmt.where(cast(Logistica.meli_status, Text) == "{}")
    stmt = stmt.order_by(
        Logistica.data.desc().nulls_last(), Logistica.created_at.desc()
    ).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()

    cache: dict[str, ShopeeClient] = {}
    updated = 0
    skipped = 0
    failed = 0
    for row in rows:
        try:
            if await enrich_row(session, row, client_cache=cache):
                updated += 1
        except ShopeeEnrichError:
            skipped += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            logger.warning(
                "logistica_shopee_row_failed",
                id=str(row.id), pedido=row.pedido_marketplace, err=str(e)[:200],
            )
    await session.commit()
    summary = {"seen": len(rows), "updated": updated, "skipped": skipped, "failed": failed}
    logger.info("logistica_shopee_enrich_batch", **summary)
    return summary
