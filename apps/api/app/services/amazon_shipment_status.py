"""Confirmação de envio físico compartilhada pelos fluxos Amazon."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

AMAZON_SHIPPED = frozenset({"Shipped"})
AMAZON_EASYSHIP_SAIU = frozenset({
    "PickedUp",
    "DroppedOff",
    "AtDestinationFC",
    "OutForDelivery",
    "Delivered",
    "RejectedByBuyer",
    "Undeliverable",
    "ReturningToSeller",
    "ReturnedToSeller",
    "Damaged",
    "Lost",
})


def amazon_shipment_confirmed(status: Mapping[str, Any] | None) -> bool:
    """True somente quando há confirmação de saída do pacote.

    Em pedidos do vendedor, ``Shipped`` pode aparecer na emissão da NF;
    EasyShip ausente não comprova coleta. AFN identifica explicitamente
    expedição pela Amazon (FBA), em que ``Shipped`` basta se não houver um
    estado EasyShip que contradiga o envio. Estado desconhecido não confirma.
    """
    if not status:
        return False
    order_status = str(status.get("order_status") or "").strip()
    if order_status not in AMAZON_SHIPPED:
        return False
    easyship = str(status.get("easyship_status") or "").strip()
    if easyship:
        return easyship in AMAZON_EASYSHIP_SAIU
    return str(status.get("fulfillment_channel") or "").strip() == "AFN"
