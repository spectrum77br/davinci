"""Shadow check: the same reads asked of Bling and of the ERP's compatible API, compared."""

import json
import os
import sqlite3

import pytest

from app.erp_local import bling_compat, compat_shadow
from app.erp_local import bling_shadow_probe as probe
from app.erp_local.core import ERPError, dumps


def with_origin(erp):
    """Every demo product carries its Bling id, as the live base does."""
    with erp.connection() as db:
        skus = [r[0] for r in db.execute("SELECT sku FROM products ORDER BY sku")]
        for number, sku in enumerate(skus, start=1001):
            db.execute(
                "INSERT OR REPLACE INTO catalog_origin VALUES (?,?)",
                (sku, dumps({"bling_product_id": str(number), "stock_state": "known"})),
            )
    return skus


def bling_like(erp, change=None):
    """A Bling that answers what the ERP answers, then `change` edits the bodies."""

    def read(request):
        asked = [(None, c["path"], c["params"]) for c in request["calls"]]
        results = compat_shadow.ask_erp(compat_shadow.ReadOnlyERP(erp.database), asked)
        if change:
            change(asked, results)
        return probe.report("read_ok", results)

    return read


def test_the_probe_takes_only_the_listed_reads():
    sample = [{"sku": "demo.fone", "id": 1001}]
    asked = [{"path": p, "params": q} for _, p, q in compat_shadow.calls(sample)]
    assert probe.checked(asked) == asked
    for wrong in (
        [{"path": "/contatos", "params": []}],
        [{"path": "/pedidos/vendas", "params": []}],
        [{"path": "/nfe/1", "params": []}],
        [{"path": "/produtos", "params": [["dataInicial", "2026-01-01"]]}],
        [{"path": "/produtos", "params": [], "method": "POST"}],
        [{"path": "/depositos", "params": []}] * (probe.MAX_CALLS + 1),
    ):
        with pytest.raises(ValueError):
            probe.checked(wrong)


def test_fields_compare_by_item_id_and_balances_apart():
    bling = {
        "data": [
            {"id": 1, "nome": "Fone", "preco": 10, "estoque": {"saldoVirtualTotal": 5}},
            {"id": 2, "nome": "Capa"},
        ]
    }
    erp = {
        "data": [
            {"id": 3, "nome": "Outro"},
            {"id": 1, "nome": "Fone ", "preco": 10.0, "estoque": {"saldoVirtualTotal": 4}},
        ]
    }
    found = compat_shadow.compare(bling, erp)
    kinds = {path: kind for path, kind, _, _ in found["fields"]}
    assert kinds == {
        "data[id=1].id": "equal",
        "data[id=1].nome": "different",
        "data[id=1].preco": "equal",
        "data[id=1].estoque.saldoVirtualTotal": "moving",
    }
    assert found["only_bling_items"] == 1 and found["only_erp_items"] == 1
    assert compat_shadow.same(1, 1.0) and not compat_shadow.same("1", 1)
    assert not compat_shadow.same(True, 1)


def test_round_compares_and_never_writes_the_base_it_reads(erp):
    skus = with_origin(erp)
    before = os.environ.get(bling_compat.TOKEN_ENV)

    def change(asked, results):
        for (_, path, params), result in zip(asked, results, strict=True):
            body = result["body"]
            if path == "/produtos" and params and isinstance(body.get("data"), list):
                for item in body["data"]:
                    item["nome"] = item["nome"] + " (Bling)"
                    item["campoNovo"] = 1
            if path == "/estoques/saldos":
                for item in body["data"]:
                    item["saldoFisicoTotal"] = item["saldoFisicoTotal"] + 1

    result = compat_shadow.run(erp, bling_like(erp, change), now=0)
    assert result["state"] == "read_ok" and result["errors"] == 0
    assert result["different"] >= 1 and result["missing"] >= 1 and result["moving"] >= 1
    assert os.environ.get(bling_compat.TOKEN_ENV) == before
    with erp.connection() as db:
        report = compat_shadow.latest(db)
    assert report["rounds"] == 1 and report["states"] == {"read_ok": 1}
    assert report["sample"] == sorted(report["sample"]) and set(report["sample"]) <= set(skus)
    paths = {(p["route"], p["path"], p["kind"]) for p in report["paths"]}
    assert ("produto_codigo", "data[].nome", "different") in paths
    assert ("produto_codigo", "data[].campoNovo", "missing") in paths
    assert ("saldos", "data[].saldoFisicoTotal", "moving") in paths
    routes = {r["route"]: r for r in report["routes"]}
    assert routes["depositos"]["different"] == routes["depositos"]["missing"] == 0
    with compat_shadow.ReadOnlyERP(erp.database).connection() as db:
        with pytest.raises(sqlite3.OperationalError):
            db.execute("CREATE TABLE written (a)")


def test_a_read_without_token_is_kept_without_comparing(erp):
    with_origin(erp)
    result = compat_shadow.run(erp, lambda request: probe.report("token_expired"), now=0)
    assert result["state"] == "token_expired"
    with erp.connection() as db:
        report = compat_shadow.latest(db)
    assert report["state"] == "token_expired" and report["routes"] == []


def test_a_read_out_of_shape_is_refused(erp):
    with_origin(erp)

    def short(request):
        return probe.report("read_ok", [{"status": 200, "body": {}}])

    with pytest.raises(ERPError) as refused:
        compat_shadow.run(erp, short, now=0)
    assert refused.value.code == "shadow_read_invalid"
    with erp.connection() as db:
        assert compat_shadow.latest(db) is None


def test_errors_on_either_side_are_listed(erp):
    with_origin(erp)

    def change(asked, results):
        results[2] = {"status": 429, "body": None}

    compat_shadow.run(erp, bling_like(erp, change), now=0)
    with erp.connection() as db:
        report = compat_shadow.latest(db)
    (depots,) = [r for r in report["routes"] if r["route"] == "depositos"]
    assert depots["errors"] == [{"ref": "/depositos", "bling": 429, "erp": 200}]
    assert json.dumps(report)
