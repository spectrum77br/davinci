"""Amazon-only shipment evidence and guarded Bling application.

A seller's ``Shipped`` is commercial confirmation, not physical dispatch.
The Correios fallback requires a matching registered tracking code, positive
local evidence AND a fresh, timestamped carrier history. A fresh Bling read
binds that evidence to every current package before any status is changed.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingEnvioEvento, BlingOrder
from app.models.logistica import Logistica
from app.services import logistica_track
from app.services.bling_situacoes import SITUACOES_ENVIADO_ETIQUETA_STR
from app.services.logistica_amazon_canal import CANAL_DBA, canal_por_servico_bling
from app.services.margem_audit import record_margem_audit
from app.services.marketplaces.bling import BlingClient

_BRT = ZoneInfo("America/Sao_Paulo")
_OPEN = (*SITUACOES_ENVIADO_ETIQUETA_STR, "6")
_TRACKING = re.compile(r"[A-Z]{2}\d{9}BR\Z")
# Deliberately an inclusion list. Unknown text, errors, invoice, label,
# pre-advice and promised collection must never imply physical dispatch.
_PHYSICAL = frozenset(
    {
        "objeto postado",
        "objeto coletado",
        "objeto em transferencia",
        "objeto em transito",
        "objeto encaminhado",
        "objeto saiu para entrega ao destinatario",
        "objeto saiu para entrega",
        "objeto entregue ao destinatario",
    }
)


@dataclass(frozen=True)
class AmazonShipmentConfirmation:
    shipped_at: datetime | None
    tracking_numbers: tuple[str, ...] = ()
    logistics_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True)
class AmazonShipmentApplied:
    local_updated: int = 0
    bling_updated: bool = False
    error: bool = False
    reason: str = "skipped"


def _physical_event(description: str | None) -> bool:
    txt = (
        unicodedata.normalize("NFKD", str(description or ""))
        .encode("ascii", "ignore")
        .decode()
        .lower()
        .strip()
    )
    # Localizacao adds ``city/UF —``; the carrier history is the bare text.
    if " — " in str(description or ""):
        txt = (
            unicodedata.normalize("NFKD", str(description).rsplit(" — ", 1)[-1])
            .encode("ascii", "ignore")
            .decode()
            .lower()
            .strip()
        )
    # The carrier appends this exact informational suffix to transfer events.
    txt = txt.removesuffix(" - por favor aguarde").strip().rstrip(".")
    return txt in _PHYSICAL


def _order_start(order: BlingOrder) -> datetime | None:
    if not isinstance(order.data, datetime) or order.data.tzinfo is None:
        return None
    # Date-only Bling input is already BRT midnight. Preserve any real time
    # precision rather than accepting a package event before the purchase.
    return order.data.astimezone(UTC)


def _within_order(value: datetime | None, start: datetime, now: datetime) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and start <= value <= now


def _local_evidence(row: Logistica, order: BlingOrder, now: datetime) -> bool:
    start = _order_start(order)
    number = str(row.rastreio or "").strip().upper()
    return bool(
        start is not None
        and str(row.plataforma or "").strip().lower() == "amazon"
        and row.pedido_bling == order.numero
        and row.pedido_marketplace == order.numeroloja
        and row.amazon_canal == "proprio"
        and canal_por_servico_bling(row.servico_envio) != CANAL_DBA
        and _TRACKING.fullmatch(number)
        and number == str(row.rastreio_17track or "").strip().upper()
        and _within_order(row.localizacao_at, start, now)
        and _physical_event(row.localizacao)
    )


async def load_amazon_correios_confirmations(
    session: AsyncSession,
    orders: list[BlingOrder],
) -> dict[int, AmazonShipmentConfirmation]:
    """Call only for fresh Amazon Shipped + MFN + absent EasyShip responses.

    Uses one bounded 17track batch for candidates with positive local evidence;
    network/parser failures return no confirmations. Dates come from the
    carrier event, never localizacao_at (which is only an observation time).
    """
    targets = [o for o in orders if o.bling_id and o.numero and o.numeroloja and o.loja]
    if not targets:
        return {}
    rows = (
        await session.scalars(
            select(Logistica).where(
                func.lower(func.trim(Logistica.plataforma)) == "amazon",
                Logistica.pedido_bling.in_([o.numero for o in targets]),
                Logistica.pedido_marketplace.in_([o.numeroloja for o in targets]),
            )
        )
    ).all()
    now = datetime.now(UTC)
    candidates: dict[int, list[Logistica]] = {}
    for order in targets:
        matching = [r for r in rows if _local_evidence(r, order, now)]
        if matching:
            candidates[int(order.bling_id)] = matching
    numbers = sorted({str(r.rastreio).strip().upper() for rs in candidates.values() for r in rs})
    if not numbers:
        return {}
    # A tracking code attached to distinct orders is ambiguous even when one
    # row happens to contain a positive event. Do not silently pick an owner.
    owners = (
        await session.execute(
            select(Logistica.rastreio, Logistica.pedido_bling, Logistica.pedido_marketplace).where(
                func.upper(func.trim(Logistica.rastreio)).in_(numbers)
            )
        )
    ).all()
    identities: dict[str, set[tuple[str | None, str | None]]] = {}
    for tracking, numero, marketplace_code in owners:
        identities.setdefault(str(tracking).strip().upper(), set()).add((numero, marketplace_code))
    ambiguous = {n for n, values in identities.items() if len(values) != 1}
    candidates = {
        bid: rs
        for bid, rs in candidates.items()
        if not any(str(r.rastreio).strip().upper() in ambiguous for r in rs)
    }
    numbers = sorted({str(r.rastreio).strip().upper() for rs in candidates.values() for r in rs})
    if not numbers:
        return {}
    try:
        histories = await logistica_track.eventos(numbers)
    except (httpx.HTTPError, logistica_track.Track17Error, ValueError, TypeError, AttributeError):
        return {}
    confirmations = {}
    for order in targets:
        matching = candidates.get(int(order.bling_id), [])
        if not matching:
            continue
        start = _order_start(order)
        dates: list[datetime] = []
        tracks = sorted({str(r.rastreio).strip().upper() for r in matching})
        for number in tracks:
            events = histories.get(number) or []
            # A future event invalidates this history instead of falling back
            # to an older event from a potentially corrupt/reused tracking.
            if not events or any(
                not isinstance(e.quando, datetime) or e.quando.tzinfo is None or e.quando > now
                for e in events
            ):
                break
            latest_at = max(e.quando for e in events)
            # The newest carrier result must positively confirm movement.
            # Never prefer an older posting over current pre-posting/unknown
            # data, which can signal a recycled number or mismatched package.
            if any(not _physical_event(e.descricao) for e in events if e.quando == latest_at):
                break
            if any(_physical_event(e.descricao) and e.quando < start for e in events):
                break
            physical = [
                e.quando
                for e in events
                if _physical_event(e.descricao) and _within_order(e.quando, start, now)
            ]
            if not physical:
                break
            dates.append(min(physical))
        else:
            confirmations[int(order.bling_id)] = AmazonShipmentConfirmation(
                shipped_at=max(dates),
                tracking_numbers=tuple(tracks),
                logistics_ids=tuple(r.id for r in matching),
            )
    return confirmations


def _tracking_matches(data: dict, confirmation: AmazonShipmentConfirmation) -> bool:
    if not confirmation.tracking_numbers:
        return True  # Native positive Amazon EasyShip/FBA confirmation.
    transport = data.get("transporte")
    if not isinstance(transport, dict):
        return False
    volumes = transport.get("volumes")
    if not isinstance(volumes, list) or not volumes:
        return False
    numbers = []
    for volume in volumes:
        if (
            not isinstance(volume, dict)
            or canal_por_servico_bling(volume.get("servico")) == CANAL_DBA
        ):
            return False
        number = str(volume.get("codigoRastreamento") or "").strip().upper()
        if not _TRACKING.fullmatch(number):
            return False
        numbers.append(number)
    return set(numbers) == set(confirmation.tracking_numbers)


async def apply_amazon_shipment(
    session: AsyncSession,
    bling: BlingClient,
    candidate: BlingOrder,
    confirmation: AmazonShipmentConfirmation,
) -> AmazonShipmentApplied:
    """Bling first, verified readback second, local stamp last. No alerts.

    Recheck identity and current state under the ingest lock. Never infer a
    successful PATCH from an HTTP error, and don't override cancellations,
    delivered orders or a previously recorded dispatch rolled back by a human.
    """
    bid, numero, code, loja = (
        candidate.bling_id,
        candidate.numero,
        candidate.numeroloja,
        candidate.loja,
    )
    if not bid or not numero or not code or not loja:
        return AmazonShipmentApplied(reason="identity_missing")
    now, shipped_at = datetime.now(UTC), confirmation.shipped_at
    if shipped_at is not None and (shipped_at.tzinfo is None or shipped_at > now):
        return AmazonShipmentApplied(reason="invalid_shipped_at")
    if confirmation.tracking_numbers and (shipped_at is None or not confirmation.logistics_ids):
        return AmazonShipmentApplied(reason="tracking_evidence_missing")
    locked = await session.scalar(
        text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
        {"key": f"ingest_bling_order:{bid}"},
    )
    if not locked:
        return AmazonShipmentApplied(reason="ingest_running")
    rows = (
        await session.scalars(
            select(BlingOrder)
            .where(BlingOrder.bling_id == bid)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()
    if not rows or any(
        r.numero != numero
        or r.loja != loja
        or r.numeroloja != code
        or r.situacao not in _OPEN
        or r.item_index is None
        for r in rows
    ):
        return AmazonShipmentApplied(reason="local_state_changed")
    if (
        await session.scalar(
            select(BlingEnvioEvento.bling_id).where(BlingEnvioEvento.bling_id == bid).limit(1)
        )
        is not None
    ):
        return AmazonShipmentApplied(reason="historical_dispatch_exists")
    if confirmation.tracking_numbers:
        start = _order_start(rows[0])
        if start is None or not _within_order(shipped_at, start, now):
            return AmazonShipmentApplied(reason="invalid_shipped_at")
        evidence = (
            await session.scalars(
                select(Logistica)
                .where(Logistica.id.in_(confirmation.logistics_ids))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).all()
        if (
            len(evidence) != len(confirmation.logistics_ids)
            or any(not _local_evidence(r, rows[0], now) for r in evidence)
            or {str(r.rastreio).strip().upper() for r in evidence}
            != set(confirmation.tracking_numbers)
        ):
            return AmazonShipmentApplied(reason="tracking_evidence_changed")

    async def read_bling() -> dict | None:
        response = await bling._request("GET", f"/pedidos/vendas/{bid}")
        if response.status_code != 200:
            return None
        data = response.json().get("data")
        if (
            not isinstance(data, dict)
            or str(data.get("id")) != str(bid)
            or str(data.get("numero")) != numero
            or str(data.get("numeroLoja") or "").strip() != code.strip()
            or not isinstance(data.get("loja"), dict)
            or str(data["loja"].get("id")) != loja
            or not _tracking_matches(data, confirmation)
        ):
            return None
        return data

    bling_updated = False
    try:
        live = await read_bling()
        if live is None:
            return AmazonShipmentApplied(error=True, reason="bling_identity_or_read_failed")
        state = str((live.get("situacao") or {}).get("id"))
        if state not in (*_OPEN, "15"):
            return AmazonShipmentApplied(reason="bling_state_changed")
        if state != "15":
            try:
                bling_updated = await bling.update_order_situacao(bid, 15)
            except httpx.HTTPError:
                pass
        verified = await read_bling()
        if verified is None or str((verified.get("situacao") or {}).get("id")) != "15":
            return AmazonShipmentApplied(
                bling_updated=bling_updated, error=True, reason="bling_state_not_confirmed"
            )
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return AmazonShipmentApplied(
            bling_updated=bling_updated, error=True, reason="bling_read_failed"
        )
    previous = rows[0].situacao
    for row in rows:
        row.situacao = "15"
        # Keep existing operational dates, as the original shipment sweep did.
        if row.em_andamento_data is None:
            ship_day = (shipped_at or now).astimezone(_BRT).date()
            # Preserve the sweep's existing operational Sunday-to-Monday rule.
            if ship_day.weekday() == 6:
                ship_day += timedelta(days=1)
            row.em_andamento_data = ship_day
    await session.flush()
    await record_margem_audit(
        session,
        acao="situacao",
        pedido_bling=numero,
        bling_id=bid,
        sku=rows[0].item_codigo,
        valor_antigo=previous,
        valor_novo=15,
        origem="job_envio_amazon_correios" if confirmation.tracking_numbers else "job_envio_amazon",
        mudado_por=None,
    )
    return AmazonShipmentApplied(
        local_updated=len(rows), bling_updated=bling_updated, reason="confirmed"
    )
