"""Preenche a assinatura de status do TikTok (`Logistica.meli_status`) + rastreio
+ localização física puxando das APIs 202309 do TikTok Shop.

Espelha o `logistica_shopee.py`. O TikTok tem vocabulário PRÓPRIO e enxuto: o
sinal de pós-venda que importa pro fluxo já vem no `status` do pedido (Order API
202309). Então a assinatura do TikTok é um único campo:

    {"order_status": "IN_TRANSIT" | "DELIVERED" | "CANCELLED" | ...}

renderizado em PT por `logistica_rules.assinatura_tiktok`. O rastreio vem do
`tracking_number` do pedido e a localização física dos eventos de tracking
(Fulfillment API — a `description` em inglês do último evento).

Só se aplica a pedidos TikTok. Best-effort: pedido que o TikTok não devolve fica
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
from app.services.marketplaces.tiktok import TikTokClient

logger = structlog.get_logger()

_TIKTOK_PLATAFORMAS = logistica_rules._TIKTOK_PLATAFORMAS


def _tiktok_localizacao(track: dict) -> str | None:
    """Descrição (inglês) do evento de tracking de maior `update_time_millis` —
    o local/estágio físico mais recente que o TikTok expõe."""
    eventos = track.get("tracking") or []
    if not eventos:
        return None
    top = max(eventos, key=lambda e: (e or {}).get("update_time_millis") or 0)
    desc = ((top or {}).get("description") or "").strip()
    return desc or None


def _tiktok_destino(order: dict) -> str | None:
    """Destino do pedido a partir de `recipient_address.district_info` (níveis
    do endereço). Fallback de localização quando ainda não há eventos de
    tracking. Junta os nomes não-vazios (mais específico por último)."""
    addr = order.get("recipient_address") or {}
    districts = addr.get("district_info") or []
    nomes = [((d or {}).get("address_name") or "").strip() for d in districts]
    nomes = [n for n in nomes if n]
    if not nomes:
        return None
    return " - ".join(nomes)


async def build_enrichment(client: TikTokClient, order_id: str) -> dict:
    """Monta a assinatura do TikTok + rastreio + localização física.

    Retorna `{"meli_status": {"order_status": ...} | {}, "rastreio": str | None,
    "localizacao": str | None}`. Campos que o TikTok não devolver ficam de fora
    / None."""
    order_id = str(order_id)
    order = await client.get_order_detail(order_id)
    st = (order.get("status") or "").strip().upper()
    meli: dict[str, str] = {}
    if st:
        meli["order_status"] = st

    rastreio = (order.get("tracking_number") or "").strip() or None

    track = await client.get_tracking(order_id)
    localizacao = _tiktok_localizacao(track)
    if not localizacao:
        localizacao = _tiktok_destino(order)

    return {"meli_status": meli, "rastreio": rastreio, "localizacao": localizacao}


async def _tiktok_integration_for_conta(
    session: AsyncSession, conta: str | None
) -> Integration | None:
    """Integração TikTok cuja `name` casa (trim+lower) com a `conta` da linha."""
    key = (conta or "").strip().lower()
    if not key:
        return None
    rows = (
        await session.execute(
            select(Integration).where(Integration.platform == IntegrationPlatform.TIKTOK)
        )
    ).scalars().all()
    for it in rows:
        if (it.name or "").strip().lower() == key:
            return it
    return None


def _build_tiktok_client(session: AsyncSession, integration: Integration) -> TikTokClient:
    creds = decrypt_json(integration.credentials)

    async def _persist(new_creds: dict) -> None:
        integration.credentials = encrypt_json(new_creds)
        exp = new_creds.get("token_expires_at") or new_creds.get("expires_at")
        if exp:
            integration.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        await session.flush()

    return TikTokClient(creds, on_token_refresh=_persist)


class TikTokEnrichError(Exception):
    """Falha de negócio ao enriquecer uma linha TikTok (código pro endpoint)."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


async def enrich_row(
    session: AsyncSession,
    row: Logistica,
    *,
    client_cache: dict[str, TikTokClient] | None = None,
) -> bool:
    """Preenche `row.meli_status`/rastreio/localizacao puxando do TikTok. Retorna
    True se atualizou.

    Levanta `TikTokEnrichError` com código quando não dá pra prosseguir (linha
    não-TikTok, sem pedido de marketplace, conta sem integração TikTok)."""
    if (row.plataforma or "").strip().lower() not in _TIKTOK_PLATAFORMAS:
        raise TikTokEnrichError("logistica_nao_tiktok")
    order_id = (row.pedido_marketplace or "").strip()
    if not order_id:
        raise TikTokEnrichError("logistica_sem_pedido")

    conta = row.conta
    client: TikTokClient | None = None
    if client_cache is not None and conta in client_cache:
        client = client_cache[conta]
    else:
        integ = await _tiktok_integration_for_conta(session, conta)
        if integ is None:
            raise TikTokEnrichError("logistica_sem_integracao")
        client = _build_tiktok_client(session, integ)
        if client_cache is not None:
            client_cache[conta] = client

    enr = await build_enrichment(client, order_id)
    row.meli_status = enr["meli_status"]
    if enr.get("rastreio"):
        row.rastreio = enr["rastreio"]
    if enr.get("localizacao"):
        row.localizacao = enr["localizacao"]
    # Divergência TikTok: order_status comercial × último evento físico.
    row.divergencia = logistica_rules.detectar_divergencia_tiktok(
        row.meli_status, row.localizacao
    )
    return True


async def enrich_recent(
    session: AsyncSession,
    *,
    limit: int = 100,
    only_empty: bool = True,
) -> dict[str, int]:
    """Enriquece um lote de linhas TikTok (mais recentes primeiro), reusando o
    client por conta. `only_empty` pula linhas que já têm meli_status."""
    stmt = select(Logistica).where(
        func.lower(func.trim(Logistica.plataforma)).in_(tuple(_TIKTOK_PLATAFORMAS))
    )
    if only_empty:
        stmt = stmt.where(cast(Logistica.meli_status, Text) == "{}")
    stmt = stmt.order_by(
        Logistica.data.desc().nulls_last(), Logistica.created_at.desc()
    ).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()

    cache: dict[str, TikTokClient] = {}
    updated = 0
    skipped = 0
    failed = 0
    for row in rows:
        try:
            if await enrich_row(session, row, client_cache=cache):
                updated += 1
        except TikTokEnrichError:
            skipped += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            logger.warning(
                "logistica_tiktok_row_failed",
                id=str(row.id), pedido=row.pedido_marketplace, err=str(e)[:200],
            )
    await session.commit()
    summary = {"seen": len(rows), "updated": updated, "skipped": skipped, "failed": failed}
    logger.info("logistica_tiktok_enrich_batch", **summary)
    return summary
