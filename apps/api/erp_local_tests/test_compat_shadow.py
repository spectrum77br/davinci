"""Shadow check: the same reads asked of Bling and of the ERP's compatible API, compared."""

import json
import os
import sqlite3

import pytest

from app.erp_local import bling_compat, compat_shadow
from app.erp_local import bling_shadow_probe as probe
from app.erp_local.core import ERPError, LocalERP, dumps


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
        compat_shadow.number(erp)
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


def test_the_homologation_shows_the_rounds_kept_in_the_real_base(erp, tmp_path):
    from app.erp_local import homolog

    with_origin(erp)
    target = tmp_path / "copy.sqlite3"
    assert homolog.seed(erp.database, target) is True
    compat_shadow.run(erp, bling_like(erp), now=0)
    copy = LocalERP(target)
    homolog.follow(copy, erp.database)
    with copy.connection() as db:
        report = compat_shadow.latest(db)
    assert report["state"] == "read_ok" and report["totals"]["different"] == 0


BLING_DEPOT = 14887091246
NEXUS = {"id": 204437662, "descricao": "ML Nexus AG", "tipo": "MercadoLivre", "situacao": 1}


def bling_registers(asked, results):
    for index, (_, path, params) in enumerate(asked):
        if path == "/depositos":
            depot = {"id": BLING_DEPOT, "descricao": "Geral", "situacao": 1, "padrao": True}
            results[index] = {"status": 200, "body": {"data": [depot]}}
        if path == "/canais-venda":
            page = dict(params)["pagina"]
            results[index] = {"status": 200, "body": {"data": [NEXUS] if page == "1" else []}}


def test_bling_depot_id_and_channels_are_kept_and_answered_the_same(erp):
    with_origin(erp)
    compat_shadow.number(erp)  # the ERP had given its depot an id of its own
    compat_shadow.run(erp, bling_like(erp, bling_registers), now=0)
    with erp.connection() as db:
        report = compat_shadow.latest(db)
    assert report["kept"] == {"depot": BLING_DEPOT, "channels": 1}
    asked = [
        (None, "/depositos", []),
        (None, "/canais-venda", [["limite", "100"]]),
        (None, "/estoques/saldos", []),
    ]
    depots, channels, balances = (r["body"]["data"] for r in compat_shadow.ask_erp(erp, asked))
    (default,) = [d for d in depots if d["padrao"]]
    assert default["id"] == BLING_DEPOT
    assert channels[0] == NEXUS
    assert all("codigo" in b["produto"] for b in balances)
    assert any(d["id"] == BLING_DEPOT for b in balances for d in b["depositos"])
    compat_shadow.run(erp, bling_like(erp, bling_registers), now=0)
    with erp.connection() as db:
        ids = db.execute("SELECT count(*) FROM compat_ids WHERE numeric_id=?", (BLING_DEPOT,))
        assert ids.fetchone()[0] == 1


def test_product_answers_ncm_and_cost_as_bling_shows_them(erp):
    from app.erp_local import bling_compat

    assert bling_compat._ncm("95030080") == "9503.00.80"
    assert bling_compat._ncm("9503.00.80") == "9503.00.80"
    assert bling_compat._ncm("") == ""
    with_origin(erp)
    compat_shadow.number(erp)
    product = next(p for p in erp.snapshot()["products"] if not p["suppliers"]["rows"])
    with erp.connection() as db:
        identifier = bling_compat.numeric_id(db, "product", product["sku"])
    (answer,) = compat_shadow.ask_erp(erp, [(None, f"/produtos/{identifier}", [])])
    cost = bling_compat._float(product["cost_price"])
    assert (
        answer["body"]["data"]["fornecedor"]["precoCusto"]
        == cost
        == answer["body"]["data"]["precoCusto"]
    )


def test_a_product_with_a_supplier_in_the_erp_answers_in_the_read_only_shadow(erp):
    from app.erp_local import product_suppliers

    with_origin(erp)
    compat_shadow.number(erp)
    contact = erp.command(
        "teste:fornecedor",
        "contact.save",
        {"data": {"name": "Fornecedor ACME", "roles": ["supplier"]}},
    )["id"]
    with erp.connection() as db:
        version = product_suppliers.details(db, "demo.fone")["version"]
    row = {
        "contact_id": contact,
        "description": "",
        "code": "F-1",
        "purchase_price": "10",
        "cost_price": "12.5",
        "warranty_months": 0,
        "is_default": True,
    }
    erp.command(
        "teste:vinculo",
        "product.suppliers.save",
        {"sku": "demo.fone", "version": version, "rows": [row], "reason": "Teste"},
    )
    with erp.connection() as db:
        identifier = bling_compat.numeric_id(db, "product", "demo.fone")
    asked = [(None, f"/produtos/{identifier}", [])]
    (before,) = compat_shadow.ask_erp(compat_shadow.ReadOnlyERP(erp.database), asked)
    assert before["status"] == 500  # the link had no compatible id yet and the base is read only
    compat_shadow.number(erp)
    (answer,) = compat_shadow.ask_erp(compat_shadow.ReadOnlyERP(erp.database), asked)
    assert answer["status"] == 200
    supplier = answer["body"]["data"]["fornecedor"]
    assert (
        supplier["id"] > bling_compat.FIRST_OWN_ID
        and supplier["contato"]["nome"] == "Fornecedor ACME"
    )
    assert supplier["precoCusto"] == 12.5
