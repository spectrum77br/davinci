"""The status clock: every Bling situation, and cancelling releases the reservation."""

import pytest

from app.erp_local import bling_compat
from app.erp_local.core import ERPError


def sale(erp, key="s1"):
    return erp.command(
        key,
        "order.import",
        {
            "channel": "ml",
            "store": "c",
            "external_id": "X-" + key,
            "depot": "geral",
            "items": [{"sku": "demo.aparelho", "quantity": "1", "unit_price": "10"}],
            "customer_name": "Ana",
        },
    )["id"]


def current(erp, order):
    with erp.connection() as db:
        doc, _ = erp.document(db, order, "order")
        return bling_compat._current_situation(db, doc), doc["status"]


def test_custom_situation_is_only_a_label(erp):
    order = sale(erp)
    erp.command("t1", "order.compat_situation", {"id": order, "situation_id": 83962})
    assert current(erp, order) == (83962, "reserved")


def test_cancelled_releases_the_reservation(erp):
    order = sale(erp)
    erp.command("t1", "order.compat_situation", {"id": order, "situation_id": 12})
    assert current(erp, order) == (12, "cancelled")


def test_unknown_situation_and_shipped_cancel_are_refused(erp):
    order = sale(erp)
    with pytest.raises(ERPError, match="não existe"):
        erp.command("t1", "order.compat_situation", {"id": order, "situation_id": 1})
    erp.command("t2", "order.ship", {"id": order})
    with pytest.raises(ERPError, match="despachado"):
        erp.command("t3", "order.compat_situation", {"id": order, "situation_id": 12})
