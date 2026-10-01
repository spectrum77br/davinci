"""Apply a verified Magalu shipment to one Bling order, without notifications."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingEnvioEvento, BlingOrder
from app.services.bling_situacoes import SITUACOES_ENVIADO_ETIQUETA_STR
from app.services.magalu_shipment_status import MagaluShipmentStatus, normalize_magalu_order_code
from app.services.margem_audit import record_margem_audit
from app.services.marketplaces.bling import BlingClient

_OPEN = (*SITUACOES_ENVIADO_ETIQUETA_STR, "6")
_BRT = ZoneInfo("America/Sao_Paulo")


@dataclass(frozen=True)
class MagaluShipmentApplied:
    local_updated: int = 0
    bling_updated: bool = False
    error: bool = False
    reason: str = "skipped"


async def apply_magalu_shipment(
    session: AsyncSession,
    bling: BlingClient,
    candidate: BlingOrder,
    confirmation: MagaluShipmentStatus,
) -> MagaluShipmentApplied:
    """Caller owns the transaction/savepoint. Never trust an error as success.

    Lock against the normal per-order webhook ingest. Re-read both local and
    Bling state so a cancellation or manual change after candidate loading
    cannot be overwritten. Only a fresh Bling GET in state 15 permits the
    local transition; no stock/financial refresh or messaging is enqueued.
    """
    if not confirmation.confirmed or not candidate.bling_id:
        return MagaluShipmentApplied(reason="not_confirmed")
    bid, numero, raw_code, loja = (
        candidate.bling_id,
        candidate.numero,
        candidate.numeroloja,
        candidate.loja,
    )
    code = normalize_magalu_order_code(raw_code)
    if not code or not numero or not loja:
        return MagaluShipmentApplied(reason="identity_missing")
    shipped_at = confirmation.shipped_at
    if shipped_at is not None and (shipped_at.tzinfo is None or shipped_at > datetime.now(UTC)):
        return MagaluShipmentApplied(reason="invalid_shipped_at")
    locked = await session.scalar(
        text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
        {"key": f"ingest_bling_order:{bid}"},
    )
    if not locked:
        return MagaluShipmentApplied(reason="ingest_running")
    rows = (
        await session.scalars(
            select(BlingOrder)
            .where(BlingOrder.bling_id == bid)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()
    if not rows or any(
        row.numero != numero
        or row.loja != loja
        or row.numeroloja != raw_code
        or row.situacao not in _OPEN
        or row.item_index is None
        for row in rows
    ):
        return MagaluShipmentApplied(reason="local_state_changed")
    # A previous dispatch followed by a manual rollback requires review; do
    # not rewrite historical events or create the date-correction outbox.
    historical = await session.scalar(
        select(BlingEnvioEvento.bling_id).where(BlingEnvioEvento.bling_id == bid).limit(1)
    )
    if historical is not None:
        return MagaluShipmentApplied(reason="historical_dispatch_exists")

    async def read_bling() -> dict | None:
        response = await bling._request("GET", f"/pedidos/vendas/{bid}")
        if response.status_code != 200:
            return None
        data = response.json().get("data")
        if not isinstance(data, dict):
            return None
        store = data.get("loja")
        if (
            str(data.get("id")) != str(bid)
            or str(data.get("numero")) != numero
            or normalize_magalu_order_code(data.get("numeroLoja")) != code
            or not isinstance(store, dict)
            or str(store.get("id")) != loja
        ):
            return None
        return data

    bling_updated = False
    try:
        live = await read_bling()
        if live is None:
            return MagaluShipmentApplied(error=True, reason="bling_identity_or_read_failed")
        state = str((live.get("situacao") or {}).get("id"))
        if state not in (*_OPEN, "15"):
            return MagaluShipmentApplied(reason="bling_state_changed")
        if state != "15":
            # Even if PATCH fails (including 400/409/422 or a timeout), only a
            # subsequent verified state 15 can authorize the local write.
            try:
                bling_updated = await bling.update_order_situacao(bid, 15)
            except httpx.HTTPError:
                pass
        confirmed = await read_bling()
        if confirmed is None or str((confirmed.get("situacao") or {}).get("id")) != "15":
            return MagaluShipmentApplied(
                bling_updated=bling_updated, error=True, reason="bling_state_not_confirmed"
            )
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return MagaluShipmentApplied(
            bling_updated=bling_updated, error=True, reason="bling_read_failed"
        )

    old_status = rows[0].situacao
    for row in rows:
        row.situacao = "15"
        if shipped_at is not None:
            # These rows were all still pre-shipment: their date may be the
            # provisional label day. The confirmed posting date replaces it.
            row.em_andamento_data = shipped_at.astimezone(_BRT).date()
        elif row.em_andamento_data is None:
            row.em_andamento_data = datetime.now(_BRT).date()
    await session.flush()  # the normal trigger creates the dispatch events
    if shipped_at is not None:
        events = (
            await session.scalars(select(BlingEnvioEvento).where(BlingEnvioEvento.bling_id == bid))
        ).all()
        if {event.item_index for event in events} != {row.item_index for row in rows}:
            raise RuntimeError("magalu_dispatch_ledger_incomplete")
        for event in events:
            event.occurred_at = shipped_at
            event.shipping_day = (shipped_at.astimezone(_BRT) - timedelta(hours=10)).date()
    await record_margem_audit(
        session,
        acao="situacao",
        pedido_bling=numero,
        bling_id=bid,
        sku=rows[0].item_codigo,
        valor_antigo=old_status,
        valor_novo=15,
        origem="job_envio_magalu",
        mudado_por=None,
    )
    if shipped_at is not None:
        await record_margem_audit(
            session,
            acao="data_envio",
            pedido_bling=numero,
            bling_id=bid,
            sku=rows[0].item_codigo,
            valor_antigo=None,
            valor_novo=shipped_at.isoformat(),
            origem="magalu_shipping_shipped_at",
            mudado_por=None,
        )
    return MagaluShipmentApplied(
        local_updated=len(rows), bling_updated=bling_updated, reason="confirmed"
    )
