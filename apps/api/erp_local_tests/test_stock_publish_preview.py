"""The number each listing would get from the ERP, against what DaVinci sends today from Bling."""

import json

from app.erp_local import stock_publish_preview as preview

OFF = {"active": False, "prefixes": (), "minimum": 0}
ON = {"active": True, "prefixes": (), "minimum": 5}


def link(sku, stock, **extra):
    return {
        "channel": "ml",
        "store": "Loja Teste",
        "listing": "MLB1",
        "variation": "",
        "listing_stock": stock,
        "sku": sku,
        "product_stock": stock,
        "situation": "A",
        "bling_id": None,
        "dead": False,
        "archived": False,
        "vacation": False,
        **extra,
    }


def add(erp, sku, physical):
    erp.command("p-" + sku, "product.create", {"sku": sku, "name": sku, "kind": "simple"})
    erp.command(
        "e-" + sku,
        "stock.post",
        {"sku": sku, "depot": "geral", "type": "E", "quantity": str(physical), "reason": "teste"},
    )


def test_simple_and_kit_follow_the_erp_balance(erp):
    # demo.aparelho 8, demo.fone 5: the kit has 5, as Bling gives a kit its scarcest part.
    links = [link("demo.aparelho", 8), link("demo.kit", 5), link("demo.fone", 4)]
    with erp.connection() as db:
        result = preview.compare(db, links, [], OFF)
    assert result["iguais"] == 2 and result["diferentes"] == 1
    (diff,) = result["diferencas"]
    assert diff["sku"] == "demo.fone" and diff["erp_mandaria"] == 5
    assert diff["motivo"] == preview.REASONS["simples"]


def test_kit_difference_is_named(erp):
    with erp.connection() as db:
        result = preview.compare(db, [link("demo.kit", 7)], [], OFF)
    assert result["diferencas"][0]["motivo"] == preview.REASONS["kit"]
    assert result["diferencas"][0]["erp_mandaria"] == 5


def test_family_sum_matches_davinci_rule(erp):
    add(erp, "dg1.ci", 3)
    add(erp, "dg1.sp", 4)
    davinci = [{"sku": "dg1.ci", "stock": 3}, {"sku": "dg1.sp", "stock": 4}]
    links = [link("dg1.ci", 3), link("dg1.sp", 4)]
    with erp.connection() as db:
        summed = preview.compare(db, links, davinci, ON)
        below_floor = preview.compare(db, links, davinci, {**ON, "minimum": 10})
    assert summed["iguais"] == 2
    assert below_floor["iguais"] == 2
    stocks = {"dg1.ci": 3, "dg1.sp": 4}
    assert preview.published("dg1.ci", 3, True, lambda n: stocks.get(n, 0), ON) == 7
    assert preview.published("dg1.ci", 3, True, lambda n: 0, ON) == 3


def test_family_total_differs_when_a_sibling_is_missing_in_the_erp(erp):
    add(erp, "dg2.ci", 3)
    davinci = [{"sku": "dg2.ci", "stock": 3}, {"sku": "dg2.sp", "stock": 4}]
    with erp.connection() as db:
        result = preview.compare(db, [link("dg2.ci", 3)], davinci, ON)
    assert result["diferencas"][0]["motivo"] == preview.REASONS["familia"]
    assert result["diferencas"][0]["davinci_manda"] == 7
    assert result["diferencas"][0]["erp_mandaria"] == 3


def test_outside_the_erp_and_skipped_listings_are_counted_apart(erp):
    links = [
        link("nao.existe", 2),
        link("excluido.ci", 1, situation="E"),
        link("demo.aparelho", 8, dead=True),
        link("demo.aparelho", 8, vacation=True),
    ]
    with erp.connection() as db:
        result = preview.compare(db, links, [], OFF)
    assert result["anuncios"] == 2 and result["fora_da_conta"] == {"morto": 1, "ferias": 1}
    assert set(result["motivos"]) == {
        preview.REASONS["fora_do_erp"],
        preview.REASONS["fora_do_erp_inativo"],
    }


def test_unknown_balance_is_not_published_as_zero(erp):
    with erp.connection() as db:
        db.execute(
            "INSERT INTO catalog_origin VALUES (?,?)",
            ("demo.fone", json.dumps({"bling_product_id": "55", "stock_state": "pending"})),
        )
        result = preview.compare(db, [link("demo.kit", 5), link("x", 1, bling_id="55")], [], OFF)
    assert result["motivos"] == {preview.REASONS["saldo_desconhecido"]: 2}
    assert all(d["erp_mandaria"] is None for d in result["diferencas"])


def test_run_reads_only_and_writes_nothing(erp):
    with erp.connection() as db:
        before = db.execute("SELECT count(*) FROM events").fetchone()[0]

    def reader(sql):
        return [link("demo.aparelho", 8)] if sql == preview.LINKS_SQL else []

    assert preview.run(erp, reader, OFF)["iguais"] == 1
    with erp.connection() as db:
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == before


def test_inactive_product_sends_zero(erp):
    from app.erp_local import catalog_follow

    with erp.connection() as db:
        db.execute(
            "INSERT INTO catalog_origin VALUES (?,?)",
            ("demo.fone", json.dumps({"bling_product_id": "55", "stock_state": "known"})),
        )
    catalog_follow.follow_status(erp, [{"bling_product_id": "55", "situacao": "E"}])
    links = [link("demo.fone", 5, situation="E"), link("demo.fone", 0, situation="E")]
    with erp.connection() as db:
        result = preview.compare(db, links, [], OFF)
    assert result["iguais"] == 1
    assert result["diferencas"][0]["erp_mandaria"] == 0
    assert result["diferencas"][0]["motivo"] == preview.REASONS["inativo"]


def test_without_davinci_keys_the_family_rule_of_10_11_is_used_and_named(monkeypatch):
    import os

    for name in list(os.environ):
        if name.upper().startswith("ESTOQUE_FAMILIA_"):
            monkeypatch.delenv(name)
    family = preview.family_settings()
    assert family["active"] is True and family["minimum"] == 5
    assert family["source"] == "DaVinci em 10/11"


def test_daily_preview_is_written_beside_the_database(erp, tmp_path):
    from app.erp_local import service_loop

    def reader(sql):
        return [link("demo.aparelho", 8)] if sql == preview.LINKS_SQL else []

    output = tmp_path / "previa-anuncios-2026-11-10.json"
    service_loop.listing_preview(erp, output, reader)
    written = json.loads(output.read_text("utf-8"))
    assert written["iguais"] == 1 and written["soma_da_familia"]["origem"]

    def broken(sql):
        from app.erp_local.core import ERPError

        raise ERPError("source_unavailable", "fora")

    service_loop.listing_preview(erp, tmp_path / "nada.json", broken)
    assert not (tmp_path / "nada.json").exists()
