"""Novos procedimentos 11, 12 e 2/6: Tabela de Preços no produto, custo, vínculos, situações."""

import json

from fastapi.testclient import TestClient

from app.erp_local import bling_compat, price_table
from app.erp_local.api import create_app
from app.erp_local.core import ERPError

LINE = {
    "id": "l1",
    "sku": "dg052,dg053,dg054",
    "name": "uranyx A17 pro max 12.128",
    "department": "celular",
    "model": None,
    "ean": "dg052: 7908855913182, dg053: 7908855913175",
    "warranty_months": 12,
    "priority": "sp",
    "photos_url": "https://mega.nz/folder/abc",
    "packaging_url": "javascript:alert(1)",
    "certificate": "anatel",
    "certificate_number": "07197-26-18234",
    "certificate_model": "A17 Pro Max",
    "updated_at": "2026-11-16T10:00:00+00:00",
}


def reference(erp, **kinds):
    with erp.connection() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS reference_rows (kind TEXT, id TEXT, data TEXT NOT NULL,"
            " search TEXT NOT NULL, PRIMARY KEY(kind,id))"
        )
        for kind, rows in kinds.items():
            for row in rows:
                db.execute(
                    "INSERT OR REPLACE INTO reference_rows VALUES (?,?,?,'')",
                    (kind, str(row["id"]), json.dumps(row)),
                )


def update(erp, key, **fields):
    with erp.connection() as db:
        version = db.execute(
            "SELECT version FROM product_details WHERE sku='demo.aparelho'"
        ).fetchone()
    payload = {"sku": "demo.aparelho", "version": version[0] if version else 0, "data": fields}
    return erp.command(key, "product.update", payload, actor="ana")


def test_a_sku_finds_its_price_table_line_by_family(erp):
    reference(erp, pricing=[LINE])
    with erp.connection() as db:
        lines = price_table.index(db)
    info = price_table.for_sku(lines, "dg053.sp")
    assert info["department"] == "Celular" and info["ean"] == "7908855913175"
    assert info["certificate"] == "Anatel" and info["certificate_number"] == "07197-26-18234"
    assert info["model"] == "A17 Pro Max" and info["warranty_months"] == 12
    assert info["priority"] == "sp" and info["photos_url"].startswith("https://")
    assert info["packaging_url"] == ""
    assert price_table.for_sku(lines, "dg054.pi+a001.pi")["ean"] == ""
    assert price_table.for_sku(lines, "b026.12") is None
    snapshot = {p["sku"]: p for p in erp.snapshot()["products"]}
    assert snapshot["demo.aparelho"]["price_table"] is None


def test_certificate_name_shows_anvisa_and_isento_like_the_certificacoes_screen():
    # M-68: Anvisa (só manual) e Isento aparecem com inicial maiúscula, como Anatel e Inmetro.
    for stored, shown in (
        ("anatel", "Anatel"), ("inmetro", "Inmetro"), ("anvisa", "Anvisa"),
        ("isento", "Isento"), (" ANVISA ", "Anvisa"), ("", ""), (None, ""),
    ):
        lines = {"dg053": {**LINE, "certificate": stored}}
        assert price_table.for_sku(lines, "dg053.sp")["certificate"] == shown, stored


def test_every_cost_change_keeps_its_result_on_the_stock_at_that_moment(erp):
    update(erp, "c1", cost_price="100")
    update(erp, "c2", cost_price="120")
    update(erp, "c3", cost_price="70")
    with erp.connection() as db:
        rows = list(reversed(price_table.cost_changes(db)))
        quantity = sum(
            float(r[0])
            for r in db.execute("SELECT physical FROM balances WHERE sku='demo.aparelho'")
        )
        csv_text = price_table.cost_changes_csv(db)
    last_two = rows[-2:]
    assert [(float(r["cost_from"]), float(r["cost_to"])) for r in last_two] == [
        (100, 120),
        (120, 70),
    ]
    assert float(last_two[0]["result"]) == 20 * quantity
    assert float(last_two[1]["result"]) == -50 * quantity and last_two[1]["actor"] == "ana"
    assert (
        csv_text.splitlines()[0] == "data;sku;custo_anterior;custo_novo;quantidade;resultado;quem"
    )
    assert any(";-" in line for line in csv_text.splitlines()[1:])


def test_voltage_is_kept_and_only_known_values_pass(erp):
    update(erp, "v1", voltage="220V")
    with erp.connection() as db:
        data = json.loads(
            db.execute("SELECT data FROM product_details WHERE sku='demo.aparelho'").fetchone()[0]
        )
    assert data["voltage"] == "220V"
    try:
        update(erp, "v2", voltage="380V")
    except ERPError as error:
        assert error.code == "product_voltage"
    else:
        raise AssertionError("voltagem desconhecida aceita")


def test_listings_and_bling_situations_come_from_davinci(tmp_path):
    app = create_app(tmp_path / "erp.sqlite3")
    erp = app.state.erp
    erp.command("seed", "demo.seed", {})
    reference(
        erp,
        products=[{"id": "p1", "sku": "demo.aparelho"}, {"id": "p2", "sku": "outro"}],
        integrations=[{"id": "i1", "name": "Shopee Mega", "archived_at": None}],
        links=[
            {
                "id": "k1",
                "product_id": "p1",
                "integration_id": "i1",
                "platform": "shopee",
                "external_id": "A1",
                "variation_id": "V1",
                "external_sku": "demo.aparelho",
                "listing_title": "Aparelho",
                "stock": 7,
                "price": "99.90",
                "last_sync_status": "ok",
                "last_sync_at": None,
            },
            {
                "id": "k2",
                "product_id": "p2",
                "integration_id": "i1",
                "platform": "shopee",
                "external_id": "B1",
                "variation_id": "",
                "external_sku": "outro",
                "listing_title": "Outro",
                "stock": 1,
                "price": "1",
                "last_sync_status": "ok",
                "last_sync_at": None,
            },
        ],
        situations=[
            {"id": "83958", "name": "Enviado Fake", "active": False},
            {"id": "999001", "name": "Nova do Bling", "active": True},
        ],
    )
    client = TestClient(app, base_url="http://localhost")
    rows = client.get("/api/erp-local/products/demo.aparelho/links").json()["rows"]
    assert [(r["store"], r["listing"], r["variation"]) for r in rows] == [
        ("Shopee Mega", "A1", "V1")
    ]
    situations = client.get("/api/erp-local/state").json()["situations"]
    assert situations["names"]["999001"] == "Nova do Bling"
    assert "83958" in situations["inactive"] and "6" not in situations["inactive"]
    with erp.connection() as db:
        names, inactive = bling_compat.situation_names(db)
    assert "999001" in names and inactive == {"83958"}
    csv_reply = client.get("/api/erp-local/cost-changes.csv")
    assert csv_reply.status_code == 200 and csv_reply.text.startswith("data;sku;")


def test_gaps_list_active_simple_products_the_price_table_does_not_cover(erp):
    with erp.connection() as db:
        simple = [r[0] for r in db.execute("SELECT sku FROM products WHERE kind='simple'")][:2]
        for sku in simple:
            db.execute(
                "INSERT OR REPLACE INTO catalog_origin VALUES (?,?)",
                (sku, json.dumps({"sellable": True})),
            )
    reference(erp, pricing=[{**LINE, "id": "l9", "sku": "outra"}])
    with erp.connection() as db:
        gaps = price_table.gaps(db)
    assert gaps["active"] == 2 and not gaps["no_department"]
    assert sorted(r["sku"] for r in gaps["no_line"]) == sorted(simple)
    families = ",".join(sorted({price_table.family(sku) for sku in simple}))
    reference(erp, pricing=[{**LINE, "id": "l9", "sku": families, "department": ""}])
    with erp.connection() as db:
        gaps = price_table.gaps(db)
    assert not gaps["no_line"]
    assert sorted(r["sku"] for r in gaps["no_department"]) == sorted(simple)
