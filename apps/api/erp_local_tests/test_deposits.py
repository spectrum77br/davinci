import pytest
from test_core import balance, order, run
from test_sales import body

from app.erp_local import deposits
from app.erp_local.channels import preview_quantity
from app.erp_local.core import ERPError


def create(erp, **changes):
    return run(
        erp,
        "depot.save",
        {"name": "Avarias locais", "purpose": "quarantine", "exclude_from_total": True, **changes},
    )["id"]


def test_quarantine_keeps_physical_history_but_blocks_sales_and_previews(erp):
    depot = create(erp)
    run(
        erp,
        "stock.transfer",
        {
            "sku": "demo.aparelho",
            "depot": "geral",
            "destination": depot,
            "quantity": "2",
            "reason": "Conferência fictícia",
        },
    )
    assert balance(erp, depot=depot) == (2, 0, 2)
    p = next(p for p in erp.snapshot()["products"] if p["sku"] == "demo.aparelho")
    b = next(b for b in p["balances"] if b["depot"] == depot)
    assert b["in_total"] is False and b["sellable"] is False
    with pytest.raises(ERPError, match="quarentena"):
        run(erp, "order.save", body(depot=depot))
    with pytest.raises(ERPError, match="quarentena"):
        run(
            erp,
            "order.import",
            {
                "channel": "local",
                "store": "manual",
                "external_id": "OTHER",
                "depot": depot,
                "items": [{"sku": "demo.aparelho", "quantity": "1", "unit_price": "1"}],
            },
        )
    with erp.connection() as db:
        assert preview_quantity(erp, db, "demo.aparelho", depot) == (None, "depot_unavailable")


def test_default_and_inactivation_never_orphan_existing_stock_or_reservations(erp):
    other = create(
        erp, name="Novo padrão", purpose="stock", exclude_from_total=False, is_default=True
    )
    with erp.connection() as db:
        assert next(d for d in deposits.snapshot(db) if d["is_default"])["id"] == other
        geral_version = deposits.metadata(db, "geral")["version"]
    with pytest.raises(ERPError, match="saldos"):
        run(
            erp,
            "depot.save",
            {"id": "geral", "version": geral_version, "name": "Geral", "status": "inactive"},
        )
    order(erp)
    with pytest.raises(ERPError, match="reservas"):
        run(
            erp,
            "depot.save",
            {
                "id": "geral",
                "version": geral_version,
                "name": "Geral",
                "purpose": "quarantine",
                "exclude_from_total": True,
            },
        )
    with pytest.raises(ERPError, match="padrão"):
        run(
            erp,
            "depot.save",
            {"id": other, "version": 1, "name": "Novo padrão", "status": "inactive"},
        )


def test_draft_confirmation_rechecks_changed_depot_policy(erp):
    depot = create(erp, purpose="stock", exclude_from_total=False)
    draft = run(erp, "order.save", body(depot=depot))["id"]
    run(
        erp,
        "depot.save",
        {
            "id": depot,
            "version": 1,
            "name": "Agora quarentena",
            "purpose": "quarantine",
            "exclude_from_total": True,
        },
    )
    with pytest.raises(ERPError, match="quarentena"):
        run(erp, "order.confirm", {"id": draft})


def test_inactive_depot_and_stale_edit_are_rejected(erp):
    depot = create(erp, purpose="stock", exclude_from_total=False)
    run(erp, "depot.save", {"id": depot, "version": 1, "name": "Inativo", "status": "inactive"})
    with pytest.raises(ERPError, match="inativo"):
        run(erp, "inventory.open", {"depot": depot})
    with pytest.raises(ERPError, match="mudou"):
        run(erp, "depot.save", {"id": depot, "version": 1, "name": "Desatualizado"})
