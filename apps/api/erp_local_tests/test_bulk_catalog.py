"""Produtos 2 › Configuração (M-45, M-47): category and price of the selected products, the only
writes the screen may make while Bling owns the base. Nothing goes to Bling or to a store."""

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_catalog_follow import link, row
from test_consulta import LOCAL, app_client, change, mirrored, seed
from test_product_details import bling_row, with_origin

from app.erp_local import access, catalog_follow, categories, consulta, product_actions
from app.erp_local.api import create_app
from app.erp_local.catalog_metadata import apply_bling_read, apply_mirror, category_set_here
from app.erp_local.core import ERPError

CATEGORY = "/api/erp-local/catalog/bulk-category"
PRICE = "/api/erp-local/catalog/bulk-price"


def command(erp, kind, payload, actor="teste"):
    return erp.command(str(uuid4()), kind, payload, actor=actor)


def add_category(erp, name, **data):
    return command(erp, "category.save", {"data": {"name": name, **data}})["id"]


def add_product(erp, sku, price=None):
    payload = {"sku": sku, "name": "Produto " + sku}
    if price is not None:
        payload["price"] = price
    command(erp, "product.create", payload)


def prices(erp, *skus):
    with erp.connection() as db:
        return [
            (
                db.execute("SELECT price FROM product_pricing WHERE sku=?", (s,)).fetchone()
                or [None]
            )[0]
            for s in skus
        ]


def category_of(erp, sku):
    """The product's category as the screen shows it: the path of names."""
    with erp.connection() as db:
        path = categories.document_snapshot(db, sku)["path"]
    return " / ".join(p["name"] for p in path) or None


def outbound(erp):
    """What could leave the ERP: notices to DaVinci and stock for the stores."""
    with erp.connection() as db:
        return [
            db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608
            for table in ("compat_outbox", "stock_intents")
        ]


def example(erp):
    """The administrator's example: z2819 at 800,00 and z2820 at 560,00, and the list's
    categories."""
    for name in ("Celular", "Mala", "Acessórios"):
        add_category(erp, name)
    add_product(erp, "z2819", "800.00")
    add_product(erp, "z2820", "560.00")


def test_with_consulta_on_only_the_two_bulk_routes_write(tmp_path, monkeypatch):
    monkeypatch.delenv(consulta.ENV, raising=False)
    with app_client(tmp_path) as c:
        erp = c.app.state.erp
        assert seed(c).status_code == 200
        example(erp)
        mirrored(erp)
        state = c.get("/api/erp-local/state").json()["consulta"]
        assert state["active"] is True and state["writes"] == [CATEGORY, PRICE]
        before = outbound(erp)

        reply = c.post(
            CATEGORY, json={"skus": ["z2819", "z2820"], "category": "Celular"}, headers=LOCAL
        )
        assert reply.status_code == 200, reply.text
        assert reply.json() == {
            "changed": 2,
            "skipped": [],
            "message": "Categoria alterada em 2 produtos",
        }
        assert category_of(erp, "z2819") == category_of(erp, "z2820") == "Celular"

        body = {"skus": ["z2819", "z2820"], "action": "decrease", "mode": "percent", "amount": "10"}
        reply = c.post(PRICE, json=body, headers=LOCAL)
        assert reply.status_code == 200, reply.text
        assert reply.json()["changed"] == 2
        assert reply.json()["message"] == "Preço alterado em 2 produtos"
        assert prices(erp, "z2819", "z2820") == ["720.00", "504.00"]

        body = {"skus": ["z2819", "z2820"], "action": "set", "mode": "value", "amount": "800.00"}
        assert c.post(PRICE, json=body, headers=LOCAL).status_code == 200
        assert prices(erp, "z2819", "z2820") == ["800.00", "800.00"]
        assert outbound(erp) == before  # nothing for DaVinci, Bling or any store

        # The same changes through the command route stay refused, and so does everything else.
        with erp.connection() as db:
            version = categories.document_snapshot(db, "z2819")["version"]
        items = [{"sku": "z2819", "version": version, "category_id": None}]
        refused = change(c, "category.assign", {"items": items})
        assert refused.status_code == 409 and refused.json()["code"] == "consulta"
        adjust = {
            "items": [{"sku": "z2819", "version": 0}],
            "action": "increase",
            "mode": "value",
            "amount": "1",
        }
        refused = change(c, "product.price_adjust", adjust)
        assert refused.status_code == 409 and refused.json()["code"] == "consulta"
        for path in (CATEGORY + "/", PRICE + "x", "/api/erp-local/catalog/images"):
            refused = c.post(path, json={}, headers=LOCAL)
            assert refused.status_code == 409 and refused.json()["code"] == "consulta", path
        assert prices(erp, "z2819") == ["800.00"] and category_of(erp, "z2819") == "Celular"


def test_the_exception_is_by_exact_path():
    assert consulta.CONSULTA_WRITES == (CATEGORY, PRICE)
    for path in consulta.CONSULTA_WRITES:
        assert consulta.refuses("POST", path) is False
        assert consulta.refuses("POST", path + "/") is True
        assert access.route_area(path) == "cadastros" and path in access.ADMIN_ROUTES
    for path in ("/api/erp-local/commands", "/api/erp-local/catalog/images"):
        assert consulta.refuses("POST", path) is True


def test_without_the_local_header_nothing_changes(tmp_path, monkeypatch):
    monkeypatch.setenv(consulta.ENV, "1")
    with app_client(tmp_path) as c:
        erp = c.app.state.erp
        assert command(erp, "demo.seed", {})
        example(erp)
        for path, body in (
            (CATEGORY, {"skus": ["z2819"], "category": "Celular"}),
            (PRICE, {"skus": ["z2819"], "action": "set", "mode": "value", "amount": "1"}),
        ):
            reply = c.post(path, json=body)
            assert reply.status_code == 403 and reply.json()["code"] == "local_header_required"
        assert category_of(erp, "z2819") is None and prices(erp, "z2819") == ["800.0000"]


def test_behind_the_gate_only_administrators_change_and_who_is_kept(tmp_path, monkeypatch):
    owner, seller, team = "dono@x.com", "ana@x.com", "equipe@x.com"
    monkeypatch.setenv("ERP_TRUST_PROXY", "1")
    monkeypatch.setenv(access.ADMINS_ENV, owner)
    monkeypatch.setenv(consulta.ENV, "1")
    c = TestClient(create_app(tmp_path / "api.sqlite3"), base_url="http://127.0.0.1:8765")
    erp = c.app.state.erp
    command(erp, "demo.seed", {})
    example(erp)
    command(erp, "access.user.save", {"actor": seller, "profile": "vendas", "active": True})
    category = {"skus": ["z2819", "z2820"], "category": "mala"}
    price = {"skus": ["z2819"], "action": "increase", "mode": "value", "amount": "10.5"}
    for actor, role, code in (
        (seller, None, "access_denied"),  # Cadastros in the profile, but not an administrator
        (team, "user", "access_follow"),  # DaVinci's team follows while Bling owns the stores
        ("fora@x.com", None, "access_unknown"),
    ):
        headers = {**LOCAL, "x-erp-actor": actor, **({"x-erp-role": role} if role else {})}
        for path, body in ((CATEGORY, category), (PRICE, price)):
            reply = c.post(path, json=body, headers=headers)
            assert reply.status_code == 403 and reply.json()["code"] == code, (actor, path)
    assert category_of(erp, "z2819") is None and prices(erp, "z2819") == ["800.0000"]

    headers = {**LOCAL, "x-erp-actor": owner}
    assert c.post(CATEGORY, json=category, headers=headers).json()["changed"] == 2
    assert c.post(PRICE, json=price, headers=headers).json()["changes"] == [
        {"sku": "z2819", "from": "800.0000", "to": "810.50"}
    ]
    assert category_of(erp, "z2819") == "Mala"
    with erp.connection() as db:
        events = db.execute(
            "SELECT kind,actor FROM events WHERE operation_key LIKE 'bulk-%' ORDER BY seq"
        ).fetchall()
        assert [tuple(e) for e in events] == [
            ("category.assign", owner),
            ("product.price_adjust", owner),
        ]
        moved = db.execute(
            "SELECT sku,category_from,actor FROM own_product_category_history ORDER BY id"
        ).fetchall()
        assert [tuple(m) for m in moved] == [("z2819", None, owner), ("z2820", None, owner)]
        (kept,) = product_actions.price_history(db, "z2819")
        assert (kept["price_from"], kept["price_to"], kept["actor"]) == (
            "800.0000",
            "810.50",
            owner,
        )
        assert kept["operation_key"].startswith("bulk-price:") and kept["created_at"]


def test_category_by_name_with_reasons_for_what_is_left_out(erp):
    example(erp)
    add_product(erp, "z2821")
    add_product(erp, "z2822")
    command(erp, "product.delete", {"items": [{"sku": "z2822", "version": 0}]})
    first = categories.bulk_assign(erp, {"skus": ["z2821"], "category": "CELULAR"})
    assert first["changed"] == 1 and first["message"] == "Categoria alterada em 1 produto"
    result = categories.bulk_assign(
        erp,
        {"skus": ["z2819", "z2819", "nao.existe", "z2821", "z2822"], "category": "Celular"},
        "dono@x.com",
    )
    assert result["changed"] == 1
    assert result["skipped"] == [
        {"sku": "nao.existe", "reason": "Produto não encontrado no ERP."},
        {"sku": "z2821", "reason": "Já está nesta categoria."},
        {"sku": "z2822", "reason": "Produto excluído."},
    ]
    with pytest.raises(ERPError, match="Categoria não encontrada no ERP: Roupas") as error:
        categories.bulk_assign(erp, {"skus": ["z2819"], "category": "Roupas"})
    assert error.value.status == 422
    inactive = add_category(erp, "Antiga")
    command(
        erp,
        "category.save",
        {"id": inactive, "version": 1, "data": {"name": "Antiga", "status": "inactive"}},
    )
    with pytest.raises(ERPError, match="inativa"):
        categories.bulk_assign(erp, {"skus": ["z2820"], "category": "Antiga"})
    for bad in (
        {"skus": [], "category": "Mala"},
        {"skus": ["z2820"]},
        {"skus": ["z2820"], "category": 3},
    ):
        with pytest.raises(ERPError, match="Selecione|Informe|Escolha"):
            categories.bulk_assign(erp, bad)
    # A sub-category of the same name does not compete with the list's top-level one.
    add_category(erp, "Celular", parent_id=add_category(erp, "Outros"))
    assert categories.bulk_assign(erp, {"skus": ["z2820"], "category": "Celular"})["changed"] == 1
    assert category_of(erp, "z2820") == "Celular"


def test_more_than_a_hundred_go_in_batches_of_the_same_command(erp):
    add_category(erp, "Eletro")
    skus = [f"lote{n:03d}" for n in range(150)]
    for sku in skus:
        add_product(erp, sku)
    result = categories.bulk_assign(erp, {"skus": skus, "category": "Eletro"})
    assert result["changed"] == 150 and result["skipped"] == []
    with erp.connection() as db:
        sizes = [
            len(json.loads(r[0])["items"])
            for r in db.execute(
                "SELECT payload FROM events WHERE kind='category.assign' "
                "AND operation_key LIKE 'bulk-category:%' ORDER BY seq"
            )
        ]
    assert sizes == [100, 50]
    assert {category_of(erp, s) for s in skus} == {"Eletro"}


def test_the_manual_register_load_keeps_a_category_chosen_here(erp):
    add_category(erp, "Celular")
    # demo.fone never loaded (no edit of its register): the load would set its category.
    categories.bulk_assign(erp, {"skus": ["demo.fone"], "category": "Celular"})
    report = apply_mirror(
        erp,
        [
            {"sku": "demo.fone", "tipo": "P", "min_stock": 4, "categoria_nome": "Mala"},
            {"sku": "demo.aparelho", "tipo": "P", "min_stock": 0, "categoria_nome": "Mala"},
        ],
    )
    assert report["category_kept"] == 1 and report["updated"] == 2
    products = {p["sku"]: p for p in erp.snapshot()["products"]}
    assert category_of(erp, "demo.fone") == "Celular"
    assert products["demo.fone"]["details"]["stock_min"] == "4.000"  # the rest still loads
    assert category_of(erp, "demo.aparelho") == "Mala"
    with erp.connection() as db:
        assert category_set_here(db, "demo.fone") is True
        assert category_set_here(db, "demo.aparelho") is False  # set by the load itself


def test_the_bling_read_keeps_a_category_chosen_here(erp):
    add_category(erp, "Celular")
    with_origin(erp, "demo.fone", 1002)
    categories.bulk_assign(erp, {"skus": ["demo.fone"], "category": "Celular"})
    rows = [{"kind": "category", "data": {"id": "77", "name": "Malas", "parent_id": None}}]
    report = apply_bling_read(erp, [*rows, bling_row(1002)])
    assert report["updated"] == 1 and report["category_kept"] == 1
    product = next(p for p in erp.snapshot()["products"] if p["sku"] == "demo.fone")
    assert product["details"]["brand"] == "Marca Real"
    assert category_of(erp, "demo.fone") == "Celular"


def test_the_hourly_follow_never_touches_the_category(erp):
    add_category(erp, "Celular")
    link(erp, "demo.aparelho", "100")
    categories.bulk_assign(erp, {"skus": ["demo.aparelho"], "category": "Celular"})
    changed = {**row("100", "demo.aparelho"), "name": "Outro nome", "bling_cost_price": "7.00"}
    changed["categoria"] = {"id": 77, "descricao": "Malas"}  # even if the mirror carried one
    catalog_follow.follow(erp, [changed], [])
    catalog_follow.follow_status(erp, [{**changed, "situacao": "I"}])
    catalog_follow.follow_names(erp, [changed])
    catalog_follow.follow_structures(erp, [changed], [])
    assert category_of(erp, "demo.aparelho") == "Celular"
    catalog_follow.follow_renames(erp, [{**changed, "sku": "demo.aparelho2"}])
    assert category_of(erp, "demo.aparelho2") == "Celular"
    with erp.connection() as db:
        assert category_set_here(db, "demo.aparelho2") is True  # the mark follows the code
        assert db.execute("SELECT count(*) FROM own_product_category_history").fetchone()[0] == 1


def test_price_rules_all_or_nothing_in_cents(erp):
    example(erp)
    add_product(erp, "z2821")
    with erp.connection() as db:
        db.execute("UPDATE product_pricing SET price=NULL WHERE sku='z2821'")
    before = prices(erp, "z2819", "z2820", "z2821")
    body = {"skus": ["z2819", "z2821"], "action": "increase", "mode": "percent", "amount": "5"}
    with pytest.raises(ERPError, match="sem preço para reajustar: z2821"):
        product_actions.bulk_price(erp, body)
    body = {"skus": ["z2819", "z2820"], "action": "decrease", "mode": "value", "amount": "600"}
    with pytest.raises(ERPError, match="negativo o preço de z2820"):
        product_actions.bulk_price(erp, body)
    with pytest.raises(ERPError, match="não encontrado: nao.existe") as error:
        product_actions.bulk_price(
            erp, {"skus": ["z2819", "nao.existe"], "action": "set", "amount": "1"}
        )
    assert error.value.status == 404
    too_many = {"skus": [f"x{n}" for n in range(1001)], "action": "set", "amount": "1"}
    for bad, message in (
        (too_many, "1 a 1000"),
        ({"skus": ["z2819"], "action": "dobrar", "amount": "1"}, "novo preço"),
        ({"skus": ["z2819"], "action": "increase", "mode": "x", "amount": "1"}, "R\\$ ou %"),
        ({"skus": ["z2819"], "action": "set", "amount": "0"}, "positivo"),
        ({"skus": ["z2819"], "action": "set", "amount": True}, "número"),
        ({"skus": ["z2819"], "action": "set", "amount": "1", "extra": 1}, "Informe"),
    ):
        with pytest.raises(ERPError, match=message):
            product_actions.bulk_price(erp, bad)
    assert prices(erp, "z2819", "z2820", "z2821") == before

    # Novo preço is always R$, even if % comes along, and fills a product without price.
    body = {"skus": ["z2821", "z2820"], "action": "set", "mode": "percent", "amount": "99.999"}
    assert product_actions.bulk_price(erp, body)["changed"] == 2
    assert prices(erp, "z2820", "z2821") == ["100.00", "100.00"]
    body = {"skus": ["z2819"], "action": "increase", "mode": "percent", "amount": "12.345"}
    assert product_actions.bulk_price(erp, body)["changes"] == [
        {"sku": "z2819", "from": "800.0000", "to": "898.76"}
    ]


def test_the_same_operation_key_is_applied_once(erp):
    example(erp)
    body = {
        "skus": ["z2819"],
        "action": "increase",
        "mode": "value",
        "amount": "100",
        "operation_key": "tela-1",
    }
    first = product_actions.bulk_price(erp, body, "dono@x.com")
    again = product_actions.bulk_price(erp, body, "dono@x.com")
    assert first["replayed"] is False and again["replayed"] is True
    assert again["changes"] == first["changes"] and prices(erp, "z2819") == ["900.00"]
    with erp.connection() as db:
        assert len(product_actions.price_history(db, "z2819")) == 1


def test_price_history_keeps_the_editor_and_the_command_too(erp):
    example(erp)
    command(erp, "product.update", {"sku": "z2819", "version": 0, "data": {"price": "750"}}, "ana")
    command(erp, "product.update", {"sku": "z2819", "version": 1, "data": {"brand": "X"}}, "ana")
    command(
        erp,
        "product.price_adjust",
        {
            "items": [{"sku": "z2819", "version": 2}],
            "action": "set",
            "mode": "value",
            "amount": "700",
        },
        "ze",
    )
    with erp.connection() as db:
        rows = product_actions.price_history(db, "z2819")
    assert [(r["price_from"], r["price_to"], r["actor"]) for r in rows] == [
        ("750.0000", "700.00", "ze"),
        ("800.0000", "750.0000", "ana"),
    ]
    with pytest.raises(ERPError, match="sempre em R\\$"):
        command(
            erp,
            "product.price_adjust",
            {
                "items": [{"sku": "z2819", "version": 2}],
                "action": "set",
                "mode": "percent",
                "amount": "7",
            },
        )
