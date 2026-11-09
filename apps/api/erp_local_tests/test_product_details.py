import pytest
from test_core import balance, order, run

from app.erp_local import inventory, product_details, rich_text
from app.erp_local.channels import preview_quantity
from app.erp_local.core import ERPError, dumps
from app.erp_local.procurement import average


def update(erp, sku="demo.aparelho", version=0, **fields):
    return run(erp, "product.update", {"sku": sku, "version": version, "data": fields})


def test_product_edit_is_versioned_and_does_not_reprice_old_sales_or_change_stock_cost(erp):
    before = balance(erp)
    update(
        erp, name="Produto V1", unit="un", gtin="4006381333931", price="20.1234", cost_price="9.99"
    )
    o = order(erp, sku="demo.aparelho")
    update(erp, version=1, name="Produto V2", unit="CX", price="30", cost_price="12")
    doc = erp.snapshot()["orders"][0]
    assert doc["data"]["product_snapshots"]["demo.aparelho"]["name"] == "Produto V1"
    assert doc["data"]["product_snapshots"]["demo.aparelho"]["unit"] == "UN"
    assert doc["data"]["items"][0]["unit_price"] == "10.0000"
    with erp.connection() as db:
        assert product_details.details(db, "demo.aparelho")["gtin"] == "4006381333931"
        assert average(erp, db, "demo.aparelho", "geral") is None
    run(erp, "order.cancel", {"id": o["id"]})
    assert balance(erp) == before
    with pytest.raises(ERPError, match="mudou"):
        update(erp, version=1, name="Edição obsoleta")


def test_gtin_scan_resolves_once_and_ambiguous_codes_are_never_guessed(erp):
    update(erp, gtin="4006381333931", gtin_tax="96385074")
    identifier = run(erp, "inventory.open", {"depot": "geral"})["id"]
    result = run(
        erp,
        "inventory.scan",
        {"id": identifier, "read_id": "GTIN1", "sku": "4006381333931", "quantity": "1"},
    )
    assert result["sku"] == "demo.aparelho"
    update(erp, sku="demo.fone", gtin="4006381333931")
    with pytest.raises(ERPError, match="mais de um"):
        run(
            erp,
            "inventory.scan",
            {"id": identifier, "read_id": "GTIN2", "sku": "4006381333931", "quantity": "1"},
        )
    run(
        erp,
        "inventory.scan",
        {
            "id": identifier,
            "read_id": "SKU1",
            "sku": "demo.aparelho",
            "identifier_type": "sku",
            "quantity": "1",
        },
    )
    with erp.connection() as db:
        assert inventory.snapshot(erp, db)["sessions"][0]["counted_total"] == "2.000"


def test_inactivation_blocks_new_sales_without_changing_existing_reservations(erp):
    o = order(erp, sku="demo.aparelho")
    before = balance(erp)
    update(erp, status="inactive")
    assert balance(erp) == before
    assert order(erp, sku="demo.aparelho")["duplicate"]
    with pytest.raises(ERPError, match="inativo"):
        order(erp, external="NEW", sku="demo.aparelho")
    with erp.connection() as db:
        assert preview_quantity(erp, db, "demo.aparelho", "geral") == (None, "product_inactive")
    run(erp, "order.cancel", {"id": o["id"]})
    update(erp, version=1, status="active")
    assert order(erp, external="NEW", sku="demo.aparelho")["id"]


def test_create_product_can_include_unit_and_gtin(erp):
    run(erp, "product.create", {"sku": "new", "name": "Novo", "unit": "UN", "gtin": "SEM GTIN"})
    with erp.connection() as db:
        assert product_details.details(db, "new")["gtin"] == "SEM GTIN"


def test_characteristics_round_trip_and_older_rows_keep_unknown_values(erp):
    with erp.connection() as db:
        db.execute(
            "INSERT INTO product_details VALUES (?,?,?,?)",
            (
                "demo.aparelho",
                3,
                dumps(
                    {
                        "unit": "UN",
                        "gtin": "",
                        "gtin_tax": "",
                        "status": "active",
                        "description": "Linha <b>1</b>\nLinha 2",
                        "brand": "",
                        "location": "",
                        "weight_kg": "1.500",
                        "height_cm": "10.000",
                        "width_cm": None,
                        "length_cm": None,
                    }
                ),
                "2026-11-05T00:00:00Z",
            ),
        )
        old = product_details.details(db, "demo.aparelho")
    assert old["version"] == 3
    assert old["free_shipping"] is None and old["production"] == "" and old["volumes"] is None
    assert old["dimension_unit"] == "cm" and old["height_cm"] == "10.000"
    assert old["complementary_html"] == "<p>Linha &lt;b&gt;1&lt;/b&gt;<br>Linha 2</p>"
    update(
        erp,
        version=3,
        condition="refurbished",
        production="third_party",
        expiry_date="2027-02-28",
        free_shipping=False,
        net_weight_kg="0",
        gross_weight_kg="2.500",
        width_cm="30",
        dimension_unit="m",
        volumes=2,
        items_per_box="12",
        department="adultoU",
        dun=[{"code": "17891234567892", "quantity": "12"}],
        short_description_html='<p style="text-align: center; color: red" onclick="x()">Oi</p>',
        external_link="https://exemplo.test/produto",
        video_url="https://exemplo.test/video",
        notes="Só uso interno",
        tags=["Azul", "azul", "Mala"],
    )
    with erp.connection() as db:
        new = product_details.details(db, "demo.aparelho")
        rows = product_details.history(db, "demo.aparelho")
    assert new["net_weight_kg"] == "0.000" and new["gross_weight_kg"] == "2.500"
    assert new["weight_kg"] == "1.500" and new["height_cm"] == "10.000"
    assert new["free_shipping"] is False and new["volumes"] == 2
    assert new["short_description_html"] == '<p style="text-align: center">Oi</p>'
    assert new["tags"] == ["Azul", "Mala"]
    assert new["description"] == "Linha <b>1</b>\nLinha 2"
    assert [r["version"] for r in rows] == [4, 3]
    assert "free_shipping" in rows[0]["changed"] and rows[1]["changed"] is None
    update(erp, version=4, complementary_html="<p>Nova <script>alert(1)</script>versão</p>")
    with erp.connection() as db:
        new = product_details.details(db, "demo.aparelho")
    assert new["complementary_html"] == "<p>Nova versão</p>" and new["description"] == "Nova versão"


def test_box_code_counts_declared_units_and_never_one_by_default(erp):
    update(erp, gtin="4006381333931", dun=[{"code": "17891234567892", "quantity": "12"}])
    session = run(erp, "inventory.open", {"depot": "geral"})["id"]
    box = run(
        erp,
        "inventory.scan",
        {"id": session, "read_id": "CX1", "sku": "17891234567892", "quantity": "2"},
    )
    assert box["sku"] == "demo.aparelho"
    assert box["counted"] == "24.000" and box["units_per_read"] == "12.000"
    unit = run(
        erp,
        "inventory.scan",
        {"id": session, "read_id": "UN1", "sku": "4006381333931", "quantity": "1"},
    )
    assert unit["counted"] == "25.000"
    update(erp, sku="demo.fone", dun=[{"code": "17891234567892", "quantity": "6"}])
    with pytest.raises(ERPError, match="mais de um"):
        run(
            erp,
            "inventory.scan",
            {"id": session, "read_id": "CX2", "sku": "17891234567892", "quantity": "1"},
        )


def test_later_document_keeps_characteristics_frozen(erp):
    update(erp, gross_weight_kg="1.2", condition="new")
    order(erp, sku="demo.aparelho")
    update(erp, version=1, gross_weight_kg="9", condition="used")
    frozen = erp.snapshot()["orders"][0]["data"]["product_snapshots"]["demo.aparelho"]
    assert frozen["gross_weight_kg"] == "1.200" and frozen["condition"] == "new"


def test_category_saves_atomically_with_product_and_conflict_rolls_back(erp):
    created = run(erp, "category.save", {"data": {"name": "Malas"}})
    category = created["id"]
    run(
        erp,
        "product.update",
        {
            "sku": "demo.aparelho",
            "version": 0,
            "data": {"brand": "Marca A"},
            "category": {"version": 0, "category_id": category},
        },
    )
    product = next(p for p in erp.snapshot()["products"] if p["sku"] == "demo.aparelho")
    assert product["category"]["id"] == category and product["details"]["brand"] == "Marca A"
    before = erp.snapshot()
    with pytest.raises(ERPError, match="vínculo mudou"):
        run(
            erp,
            "product.update",
            {
                "sku": "demo.aparelho",
                "version": 1,
                "data": {"brand": "Marca B"},
                "category": {"version": 0, "category_id": None},
            },
        )
    assert erp.snapshot() == before


@pytest.mark.parametrize(
    "fields",
    [
        {"condition": "seminovo"},
        {"production": "fabrica"},
        {"dimension_unit": "polegadas"},
        {"department": "pets"},
        {"free_shipping": "sim"},
        {"expiry_date": "31/12/2027"},
        {"expiry_date": "2027-02-30"},
        {"volumes": 1.5},
        {"volumes": True},
        {"items_per_box": "-1"},
        {"net_weight_kg": "abc"},
        {"dun": [{"code": "17891234567892"}]},
        {"dun": [{"code": "17891234567892", "quantity": ""}]},
        {"dun": [{"code": "17891234567892", "quantity": "0"}]},
        {"dun": [{"code": "17891234567891", "quantity": "1"}]},
        {"dun": [{"code": "7891234567895", "quantity": "1"}]},
        {
            "dun": [
                {"code": "17891234567892", "quantity": "1"},
                {"code": "17891234567892", "quantity": "2"},
            ]
        },
        {"tags": "Azul"},
        {"tags": [" "]},
        {"external_link": "javascript:alert(1)"},
        {"video_url": "www.exemplo.test/sem-protocolo"},
        {"short_description_html": 7},
        {"gtin": "4006381333932"},
        {"gtin": "123"},
        {"gtin": "Ａ1234567"},
        {"unit": "UNIDADE-LONGA"},
        {"status": "deleted"},
        {"weight_kg": "-1"},
        {"price": "NaN"},
        {"unexpected": "value"},
    ],
)
def test_invalid_edit_rolls_back_every_field_and_event(erp, fields):
    before = erp.snapshot()
    with pytest.raises(ERPError):
        update(erp, name="Não deve persistir", **fields)
    assert erp.snapshot() == before


def test_own_activation_is_independent_of_historical_source_status(erp):
    with erp.connection() as db:
        db.execute(
            "INSERT INTO catalog_origin VALUES (?,?)",
            (
                "demo.aparelho",
                dumps(
                    {
                        "sellable": False,
                        "source_status": "I",
                        "catalog_state": "ready",
                        "stock_state": "known",
                    }
                ),
            ),
        )
    with pytest.raises(ERPError):
        order(erp, sku="demo.aparelho")
    update(erp, unit="UN")
    with erp.connection() as db:
        assert product_details.details(db, "demo.aparelho")["status"] == "inactive"
    update(erp, version=1, status="active")
    assert order(erp, sku="demo.aparelho")["id"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("<p>a</p><script>alert(1)</script>", "<p>a</p>"),
        ("<img src=x onerror=alert(1)>texto", "texto"),
        ("<svg><script>x</script><text>y</text></svg>z", "z"),
        ('<a href="javascript:alert(1)">l</a>', "l"),
        ('<a href=" JaVa\tScript:alert(1)">l</a>', "l"),
        ('<a href="data:text/html,x">l</a>', "l"),
        (
            '<a href="https://exemplo.test" onclick="x" target="_blank">l</a>',
            '<a href="https://exemplo.test" rel="noopener noreferrer nofollow">l</a>',
        ),
        (
            '<p style="background:url(x);text-align:RIGHT;padding-left:40px">a</p>',
            '<p style="text-align: right; padding-left: 40px">a</p>',
        ),
        ('<p align="center">a</p>', '<p style="text-align: center">a</p>'),
        ('<p style="padding-left:expression(alert(1))">a</p>', "<p>a</p>"),
        ("<b><i>a</b>", "<b><i>a</i></b>"),
        ("</p>a", "a"),
        ("&lt;script&gt;alert(1)&lt;/script&gt;", "&lt;script&gt;alert(1)&lt;/script&gt;"),
        ('<td colspan="2" rowspan="x">a</td>', '<td colspan="2">a</td>'),
        ("<p><br></p>", ""),
        ("<!-- x --><p>a</p>", "<p>a</p>"),
        ("<table><tr><td></td></tr></table>", "<table><tr><td></td></tr></table>"),
    ],
)
def test_rich_text_allowlist(raw, expected):
    assert rich_text.clean(raw) == expected
    assert rich_text.clean(expected) == expected


def test_rich_text_limit_and_plain_text():
    with pytest.raises(ERPError, match="ultrapassa"):
        rich_text.clean("a" * (rich_text.LIMIT + 1))
    assert rich_text.plain_text("<p>a<br>b</p><p>c</p>") == "a\nb\nc"
    assert rich_text.from_plain("a < b\n\nc\nd") == "<p>a &lt; b</p><p>c<br>d</p>"


def batch_items(erp, *skus):
    products = {p["sku"]: p for p in erp.snapshot()["products"]}
    return [{"sku": s, "version": products[s]["details"]["version"]} for s in skus]


def price(erp, sku):
    return next(p for p in erp.snapshot()["products"] if p["sku"] == sku)["price"]


def test_stock_limits_and_crossdocking_are_validated_and_preserved(erp):
    update(erp, stock_min="2", stock_max="10", crossdocking=3, location="A1")
    with erp.connection() as db:
        data = product_details.details(db, "demo.aparelho")
    assert (data["stock_min"], data["stock_max"], data["crossdocking"]) == ("2.000", "10.000", 3)
    with pytest.raises(ERPError, match="máximo"):
        update(erp, version=1, stock_min="11")
    with pytest.raises(ERPError, match="inteiro"):
        update(erp, version=1, crossdocking="2.5")


def test_service_has_no_stock_but_sells_alongside_physical_items(erp):
    run(
        erp, "product.create", {"sku": "serv", "name": "Conserto", "kind": "service", "price": "50"}
    )
    product = next(p for p in erp.snapshot()["products"] if p["sku"] == "serv")
    assert product["kind"] == "service" and product["balances"] == []
    with pytest.raises(ERPError, match="Serviço"):
        run(
            erp,
            "stock.post",
            {"sku": "serv", "depot": "geral", "type": "E", "quantity": "1", "reason": "teste"},
        )
    with pytest.raises(ERPError, match="Serviço"):
        run(
            erp,
            "product.create",
            {
                "sku": "kit.serv",
                "name": "Kit",
                "kind": "kit",
                "components": [{"sku": "serv", "quantity": "1"}],
            },
        )
    before = balance(erp)
    created = run(
        erp,
        "order.import",
        {
            "channel": "Teste local",
            "store": "A",
            "external_id": "SERV1",
            "depot": "geral",
            "items": [{"sku": "serv", "quantity": "1", "unit_price": "50"}],
        },
    )
    assert balance(erp) == before
    run(erp, "order.cancel", {"id": created["id"]})
    assert balance(erp) == before
    with erp.connection() as db:
        assert preview_quantity(erp, db, "serv", "geral") == (None, "product_service")


def test_product_type_change_refuses_stock_and_structure_dependencies(erp):
    update_type = {"sku": "demo.aparelho", "version": 0, "data": {}, "type": "service"}
    with pytest.raises(ERPError, match="estoque"):
        run(erp, "product.update", update_type)
    run(erp, "product.create", {"sku": "novo", "name": "Novo"})
    run(erp, "product.update", {"sku": "novo", "version": 0, "data": {}, "type": "service"})
    assert next(p for p in erp.snapshot()["products"] if p["sku"] == "novo")["kind"] == "service"
    run(erp, "product.update", {"sku": "novo", "version": 1, "data": {}, "type": "product"})
    assert next(p for p in erp.snapshot()["products"] if p["sku"] == "novo")["kind"] == "simple"
    run(
        erp,
        "product.create",
        {
            "sku": "kit.novo",
            "name": "K",
            "kind": "kit",
            "components": [{"sku": "novo", "quantity": "2"}],
        },
    )
    with pytest.raises(ERPError, match="compõe"):
        run(erp, "product.update", {"sku": "novo", "version": 2, "data": {}, "type": "service"})
    with pytest.raises(ERPError, match="composição"):
        run(erp, "product.update", {"sku": "kit.novo", "version": 0, "data": {}, "type": "service"})


def test_batch_status_and_tags_are_atomic_across_selection(erp):
    update(erp, tags=["Azul"])
    items = batch_items(erp, "demo.aparelho", "demo.fone")
    run(erp, "product.batch", {"items": items, "add_tags": ["azul", "Mala"]})
    products = {p["sku"]: p for p in erp.snapshot()["products"]}
    assert products["demo.aparelho"]["details"]["tags"] == ["Azul", "Mala"]
    assert products["demo.fone"]["details"]["tags"] == ["azul", "Mala"]
    before = erp.snapshot()
    stale = batch_items(erp, "demo.aparelho", "demo.fone")
    stale[1]["version"] = 0
    with pytest.raises(ERPError, match="mudou"):
        run(erp, "product.batch", {"items": stale, "status": "inactive"})
    assert erp.snapshot() == before
    run(erp, "product.batch", {"items": batch_items(erp, "demo.fone"), "status": "inactive"})
    with pytest.raises(ERPError):
        run(erp, "product.batch", {"items": batch_items(erp, "demo.fone"), "status": "deleted"})


def test_price_adjust_is_exact_atomic_and_never_publishes(erp):
    update(erp, price="100")
    update(erp, sku="demo.fone", price="10")
    items = batch_items(erp, "demo.aparelho", "demo.fone")
    result = run(
        erp,
        "product.price_adjust",
        {"items": items, "action": "increase", "mode": "percent", "amount": "12.5"},
    )
    assert result["published"] is False and result["external_writes"] is False
    assert price(erp, "demo.aparelho") == "112.5000" and price(erp, "demo.fone") == "11.2500"
    before = erp.snapshot()
    with pytest.raises(ERPError, match="negativo"):
        run(
            erp,
            "product.price_adjust",
            {"items": items, "action": "decrease", "mode": "value", "amount": "50"},
        )
    assert erp.snapshot() == before


def test_removal_keeps_history_blocks_sales_and_refuses_stock_or_structure(erp):
    with pytest.raises(ERPError, match="estoque"):
        run(erp, "product.delete", {"items": batch_items(erp, "demo.aparelho")})
    run(erp, "product.create", {"sku": "novo", "name": "Novo"})
    run(erp, "product.delete", {"items": batch_items(erp, "novo")})
    product = next(p for p in erp.snapshot()["products"] if p["sku"] == "novo")
    assert product["details"]["status"] == "deleted"
    with pytest.raises(ERPError, match="inativo"):
        order(erp, external="DEL", sku="novo")
    with pytest.raises(ERPError, match="Restaure"):
        update(erp, sku="novo", version=1, brand="X")
    run(erp, "product.restore", {"items": batch_items(erp, "novo")})
    product = next(p for p in erp.snapshot()["products"] if p["sku"] == "novo")
    assert product["details"]["status"] == "inactive"
    with erp.connection() as db:
        assert [r["version"] for r in product_details.history(db, "novo")] == [2, 1]


def test_clone_copies_register_without_codes_or_stock(erp):
    update(
        erp,
        brand="Marca",
        gtin="4006381333931",
        dun=[{"code": "17891234567892", "quantity": "6"}],
        tags=["Azul"],
        price="20",
    )
    run(
        erp,
        "product.update",
        {
            "sku": "demo.aparelho",
            "version": 1,
            "data": {},
            "tax": {"version": 0, "data": {"ncm": "85171300"}, "reason": "teste"},
        },
    )
    run(
        erp,
        "product.clone",
        {"sku": "demo.aparelho", "new_sku": "demo.copia", "name": "Cópia"},
    )
    products = {p["sku"]: p for p in erp.snapshot()["products"]}
    clone = products["demo.copia"]
    assert clone["details"]["brand"] == "Marca" and clone["details"]["tags"] == ["Azul"]
    assert clone["details"]["gtin"] == "" and clone["details"]["dun"] == []
    assert clone["tax"]["data"]["ncm"] == "85171300" and clone["price"] == "20.0000"
    assert all(b["physical"] == "0" or float(b["physical"]) == 0 for b in clone["balances"])
    with pytest.raises(ERPError, match="já cadastrado"):
        run(erp, "product.clone", {"sku": "demo.aparelho", "new_sku": "demo.copia", "name": "X"})
    run(
        erp,
        "product.create",
        {
            "sku": "kit.base",
            "name": "K",
            "kind": "kit",
            "components": [{"sku": "demo.aparelho", "quantity": "2"}],
        },
    )
    run(erp, "product.clone", {"sku": "kit.base", "new_sku": "kit.copia", "name": "K2"})
    products = {p["sku"]: p for p in erp.snapshot()["products"]}
    assert products["kit.copia"]["components"] == products["kit.base"]["components"]


def test_mirror_fill_only_touches_unedited_rows_and_replays_safely(erp):
    from app.erp_local.catalog_metadata import apply_mirror

    run(erp, "product.create", {"sku": "s001", "name": "Conserto"})
    update(erp, sku="demo.fone", brand="Editado")
    before = balance(erp)
    rows = [
        {"sku": "s001", "tipo": "S", "min_stock": 0, "categoria_nome": None},
        {"sku": "demo.aparelho", "tipo": "S", "min_stock": 3, "categoria_nome": "Celular"},
        {"sku": "demo.fone", "tipo": "P", "min_stock": 9, "categoria_nome": "Celular"},
        {"sku": "nao.existe", "tipo": "P", "min_stock": 1, "categoria_nome": None},
    ]
    report = apply_mirror(erp, rows)
    assert report["unknown_sku"] == 1 and report["already_edited"] == 1
    assert report["services"] == 1
    assert [r["sku"] for r in report["refused"]] == ["demo.aparelho"]
    products = {p["sku"]: p for p in erp.snapshot()["products"]}
    assert products["s001"]["kind"] == "service"
    assert products["demo.aparelho"]["kind"] == "simple"
    assert products["demo.fone"]["details"]["stock_min"] is None
    assert balance(erp) == before
    apply_mirror(
        erp, [{"sku": "demo.aparelho", "tipo": "P", "min_stock": 3, "categoria_nome": "Celular"}]
    )
    products = {p["sku"]: p for p in erp.snapshot()["products"]}
    assert products["demo.aparelho"]["details"]["stock_min"] == "3.000"
    assert products["demo.aparelho"]["category"]["path"][0]["name"] == "Celular"


def bling_row(identifier, **extra):
    return {
        "kind": "product",
        "data": {
            "id": identifier,
            "tipo": "P",
            "unidade": "un",
            "marca": "Marca Real",
            "gtin": "4006381333931",
            "gtinEmbalagem": "",
            "pesoLiquido": 0.5,
            "pesoBruto": 0,
            "volumes": 1,
            "itensPorCaixa": 0,
            "tipoProducao": "T",
            "condicao": 1,
            "freteGratis": False,
            "dataValidade": "0000-00-00",
            "descricaoCurta": "<p onclick='x()'>Oi</p>",
            "descricaoComplementar": "",
            "linkExterno": "www.sem-protocolo.test",
            "observacoes": "",
            "estoque": {"minimo": 3, "maximo": 0, "crossdocking": 0, "localizacao": "A1"},
            "dimensoes": {"largura": 0.15, "altura": 8, "profundidade": 0, "unidadeMedida": 0},
            "categoria": {"id": 77},
            "tributacao": {
                "origem": 0,
                "ncm": "42021210",
                "cest": "",
                "codigoExcecaoTipi": "0",
                "percentualTributos": 0,
                "spedTipoItem": "00",
            },
            "fornecedor": {
                "id": 9,
                "contato": {"id": 555, "nome": "Fornecedor Real"},
                "codigo": "F-1",
                "precoCusto": 12.5,
                "precoCompra": 0,
            },
            "estrutura": {"componentes": []},
            "image_paths": ["/foto.png"],
            **extra,
        },
    }


def with_origin(erp, sku, source_id):
    with erp.connection() as db:
        db.execute(
            "INSERT INTO catalog_origin VALUES (?,?)",
            (
                sku,
                dumps(
                    {
                        "bling_product_id": str(source_id),
                        "catalog_state": "ready",
                        "sellable": True,
                        "stock_state": "known",
                        "source_status": "A",
                    }
                ),
            ),
        )


def test_bling_read_fills_register_keeps_unknowns_and_never_touches_stock(erp):
    from app.erp_local.bling_compat import local_id
    from app.erp_local.catalog_metadata import apply_bling_read

    with_origin(erp, "demo.aparelho", 1001)
    update(erp, price="99")
    with erp.connection() as db:
        db.execute("DELETE FROM product_details_history WHERE sku='demo.aparelho'")
    before = balance(erp)
    rows = [
        {"kind": "category", "data": {"id": "77", "name": "Malas", "parent_id": None}},
        bling_row(1001),
        bling_row(9999),
    ]
    report = apply_bling_read(erp, rows)
    assert report["updated"] == 1 and report["unknown_sku"] == 1
    assert report["categories"] == 1 and report["with_images_at_origin"] == 1
    assert report["skipped_fields"] == {"external_link": 1}
    product = next(p for p in erp.snapshot()["products"] if p["sku"] == "demo.aparelho")
    d = product["details"]
    assert d["brand"] == "Marca Real" and d["unit"] == "UN" and d["gtin"] == "4006381333931"
    assert d["net_weight_kg"] == "0.500" and d["gross_weight_kg"] is None
    assert d["width_cm"] == "15.000" and d["height_cm"] == "800.000" and d["length_cm"] is None
    assert d["dimension_unit"] == "m" and d["production"] == "third_party"
    assert d["condition"] == "new" and d["free_shipping"] is False and d["expiry_date"] == ""
    assert d["stock_min"] == "3.000" and d["stock_max"] is None and d["location"] == "A1"
    assert d["short_description_html"] == "<p>Oi</p>" and d["external_link"] == ""
    assert product["tax"]["data"]["ncm"] == "42021210"
    assert product["tax"]["data"]["origin"] == "0"
    assert product["tax"]["data"]["tipi_exception"] == ""
    assert product["category"]["path"][0]["name"] == "Malas"
    supplier = product["suppliers"]["rows"][0]
    assert supplier["contact"]["name"] == "Fornecedor Real" and supplier["code"] == "F-1"
    assert supplier["cost_price"] == "12.5000" and supplier["is_default"] is True
    assert product["price"] == "99.0000" and balance(erp) == before
    with erp.connection() as db:
        assert local_id(db, "contact", 555) == supplier["contact_id"]
        assert local_id(db, "category", 77) == product["category"]["id"]
    again = apply_bling_read(erp, rows)
    assert again["updated"] == 0 and again["refused"] == [] and again["already_imported"] == 1


def test_bling_read_preserves_own_edits_and_flags_structure_differences(erp):
    from app.erp_local.catalog_metadata import apply_bling_read

    with_origin(erp, "demo.fone", 1002)
    update(erp, sku="demo.fone", brand="Minha marca")
    with_origin(erp, "demo.aparelho", 1001)
    report = apply_bling_read(
        erp,
        [
            bling_row(1002),
            bling_row(
                1001, estrutura={"componentes": [{"produto": {"id": 1002}, "quantidade": 1}]}
            ),
        ],
    )
    products = {p["sku"]: p for p in erp.snapshot()["products"]}
    assert report["edited_here"] == 1
    assert products["demo.fone"]["details"]["brand"] == "Minha marca"
    assert report["structure_differences"] == ["demo.aparelho"]
    assert products["demo.aparelho"]["structure"]["components"] == []


def test_bling_read_refuses_bad_service_conversion_without_stopping_others(erp):
    from app.erp_local.catalog_metadata import apply_bling_read

    with_origin(erp, "demo.aparelho", 1001)
    run(erp, "product.create", {"sku": "novo.serv", "name": "Conserto"})
    with_origin(erp, "novo.serv", 1003)
    report = apply_bling_read(erp, [bling_row(1001, tipo="S"), bling_row(1003, tipo="S", gtin="")])
    assert [r["code"] for r in report["refused"]] == ["product_has_stock"]
    assert report["services"] == 1
    products = {p["sku"]: p for p in erp.snapshot()["products"]}
    assert products["novo.serv"]["kind"] == "service"
    assert products["demo.aparelho"]["kind"] == "simple"


def test_virtual_kit_from_bling_gets_parts_and_loses_only_its_own_balance(erp):
    from app.erp_local import product_structures
    from app.erp_local.catalog_metadata import convert_virtual_kits

    erp.command("vk1", "product.create", {"sku": "demo.vk", "name": "Kit novo"})
    erp.command(
        "vk2",
        "stock.post",
        {"sku": "demo.vk", "depot": "geral", "type": "E", "quantity": "3", "reason": "abertura"},
    )
    with_origin(erp, "demo.vk", 2001)
    with_origin(erp, "demo.aparelho", 1001)
    before = balance(erp)
    rows = [
        {
            "kind": "product",
            "data": {
                "id": 2001,
                "estrutura": {
                    "tipoEstoque": "V",
                    "componentes": [{"produto": {"id": 1001}, "quantidade": 2}],
                },
            },
        }
    ]
    report = convert_virtual_kits(erp, rows)
    assert report["converted"] == 1 and report["balances_cleared"] == 1 and not report["refused"]
    with erp.connection() as db:
        _, recipes = product_structures.graph(db)
        assert erp.balance(db, "demo.vk", "geral")[0] == 0
        assert erp.product(db, "demo.vk")["kind"] == "kit"
    assert recipes["demo.vk"][0]["sku"] == "demo.aparelho"
    assert balance(erp) == before
    assert convert_virtual_kits(erp, rows)["converted"] == 0


def test_kit_with_local_parts_is_reported_and_never_overwritten(erp):
    from app.erp_local import product_structures
    from app.erp_local.catalog_metadata import convert_virtual_kits

    with erp.connection() as db:
        _, recipes = product_structures.graph(db)
    sku, mine = next((s, r) for s, r in recipes.items() if r)
    with_origin(erp, sku, 3001)
    with_origin(erp, mine[0]["sku"], 3002)
    rows = [
        {
            "kind": "product",
            "data": {
                "id": 3001,
                "estrutura": {
                    "tipoEstoque": "V",
                    "componentes": [{"produto": {"id": 3002}, "quantidade": 99}],
                },
            },
        }
    ]
    assert convert_virtual_kits(erp, rows)["conflicts"] == [sku]
    with erp.connection() as db:
        assert product_structures.graph(db)[1][sku] == mine
