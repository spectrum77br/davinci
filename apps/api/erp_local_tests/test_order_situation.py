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


def history(erp, order):
    with erp.connection() as db:
        return bling_compat.occurrences(db, order)


def test_occurrences_keep_who_when_and_the_situation_like_bling(erp):
    order = sale(erp)
    erp.command("c1", "order.compat_situation", {"id": order, "situation_id": 15}, actor="ana")
    erp.command(
        "c2",
        "order.occurrence",
        {"id": order, "date": "2030-01-02", "time": "14:44", "text": "Ligou", "situation_id": 15},
        actor="ana",
    )
    erp.command(
        "c3",
        "order.occurrence",
        {"id": order, "date": "2030-01-02", "time": "15:00", "text": "", "situation_id": 83962},
        actor="bia",
    )
    rows = history(erp, order)
    assert [(r["text"], r["situation_id"], r["actor"]) for r in rows] == [
        ("Situação alterada", 83962, "bia"),
        ("Ligou", 15, "ana"),
        ("Situação alterada", 15, "ana"),
    ]
    assert rows[0]["occurred_at"].startswith("2030-01-02T18:00")
    assert current(erp, order)[0] == 83962


def test_empty_note_without_change_is_refused_and_removal_keeps_the_audit(erp):
    order = sale(erp)
    with pytest.raises(ERPError):
        erp.command(
            "e1",
            "order.occurrence",
            {"id": order, "date": "2026-11-09", "time": "10:00", "text": " ", "situation_id": 6},
        )
    erp.command(
        "e2",
        "order.occurrence",
        {"id": order, "date": "2026-11-09", "time": "10:00", "text": "Nota", "situation_id": 6},
    )
    first = history(erp, order)[0]["id"]
    erp.command("e3", "order.occurrence.remove", {"id": order, "occurrence_id": first})
    assert history(erp, order) == []
    with erp.connection() as db:
        kept = db.execute(
            "SELECT removed_by FROM order_occurrences WHERE id=?", (first,)
        ).fetchone()
    assert kept["removed_by"] == "operador local"


def test_only_a_cancelled_order_without_effects_goes_to_the_trash(erp):
    order = sale(erp, "d1")
    with pytest.raises(ERPError, match="cancelado"):
        erp.command("d2", "order.discard", {"id": order, "reason": "teste"})
    erp.command("d3", "order.cancel", {"id": order})
    erp.command("d4", "order.discard", {"id": order, "reason": "pedido de teste"})
    with erp.connection() as db:
        _, data = erp.document(db, order, "order")
    assert data["discarded"]["reason"] == "pedido de teste"
    assert all(o["id"] != order for o in bling_compat._orders(erp))
