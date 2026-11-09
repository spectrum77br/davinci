from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from test_core import balance, order, run, stock

from app.erp_local import inventory
from app.erp_local.core import ERPError


def opened(erp, depot="geral"):
    return run(erp, "inventory.open", {"depot": depot})["id"]


def read(erp, session, sku="demo.aparelho", quantity="1", mode="inventory.scan", read_id=None):
    return run(
        erp,
        mode,
        {"id": session, "read_id": read_id or str(uuid4()), "sku": sku, "quantity": quantity},
    )


def state(erp):
    with erp.connection() as db:
        return inventory.snapshot(erp, db)


def test_inventory_reads_and_reset_never_post_stock_and_keep_history(erp):
    before = balance(erp)
    identifier = opened(erp)
    assert opened(erp) == identifier
    read(erp, identifier, quantity="2", read_id="SCAN-1")
    assert read(erp, identifier, quantity="2", read_id="SCAN-1")["duplicate"]
    read(erp, identifier, quantity="3")
    assert state(erp)["sessions"][0]["counted_total"] == "5.000"
    assert balance(erp) == before
    run(erp, "inventory.reset", {"id": identifier})
    assert not state(erp)["sessions"][0]["lines"]
    assert len(state(erp)["sessions"][0]["reads"]) == 2
    assert read(erp, identifier, quantity="2", read_id="SCAN-1")["duplicate"]
    assert not state(erp)["sessions"][0]["lines"]
    read(erp, identifier, quantity="0", mode="inventory.set")
    run(erp, "inventory.generate", {"id": identifier})
    assert balance(erp) == (0, 0, 0)
    assert balance(erp, "demo.fone") == (5, 0, 5)
    assert run(erp, "inventory.generate", {"id": identifier})["unchanged"]


def test_same_read_is_applied_once_with_concurrent_retries(erp):
    identifier = opened(erp)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: read(erp, identifier, read_id="CONCURRENT"), range(4)))
    assert sum(not r["duplicate"] for r in results) == 1
    assert state(erp)["sessions"][0]["counted_total"] == "1.000"
    with pytest.raises(ERPError, match="outros dados"):
        read(erp, identifier, quantity="2", read_id="CONCURRENT")


def test_any_physical_movement_during_count_requires_recount_even_after_reversal(erp):
    identifier = opened(erp)
    read(erp, identifier, quantity="8", mode="inventory.set")
    stock(erp, "E", "1")
    stock(erp, "S", "1")
    assert balance(erp) == (8, 0, 8)
    assert state(erp)["sessions"][0]["lines"][0]["conflict"]
    before = erp.snapshot()
    with pytest.raises(ERPError, match="movimentação física"):
        run(erp, "inventory.generate", {"id": identifier})
    assert erp.snapshot() == before


def test_balance_is_atomic_and_cannot_consume_reserved_pieces(erp):
    identifier = opened(erp)
    read(erp, identifier, quantity="7", mode="inventory.set")
    read(erp, identifier, sku="demo.fone", quantity="0", mode="inventory.set")
    order(erp, quantity="1")
    before = erp.snapshot()
    with pytest.raises(ERPError):
        run(erp, "inventory.generate", {"id": identifier})
    assert erp.snapshot() == before
    assert state(erp)["sessions"][0]["status"] == "counting"


def test_closed_empty_kit_and_new_product_counts_are_rejected(erp):
    identifier = opened(erp)
    with pytest.raises(ERPError, match="pelo menos"):
        run(erp, "inventory.generate", {"id": identifier})
    with pytest.raises(ERPError, match="peças físicas"):
        read(erp, identifier, sku="demo.kit")
    run(erp, "product.create", {"sku": "new", "name": "Novo"})
    with pytest.raises(ERPError, match="depois da abertura"):
        read(erp, identifier, sku="new")
    run(erp, "inventory.cancel", {"id": identifier})
    with pytest.raises(ERPError, match="encerrada"):
        read(erp, identifier)
    assert opened(erp) != identifier
