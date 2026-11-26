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
    "certificate_valid_until": "2028-07-30",
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


def test_the_certificacoes_sku_column_comes_first_then_the_line(erp):
    """M-69/M-71 (26/11/2026): DaVinci links the certificate to the product by the SKU column of
    Importação › Certificações (the family, without the tag). The product shows that certificate,
    its number, model and end date ("Validade", dd/mm/aaaa); a family not in the column keeps the
    certificate stored on its line. pricing_id is the line's id."""
    usm001 = {
        "id": "dg053",
        "sku": "dg053",
        "certificate": "anatel",
        "certificate_number": "08215-25-18234",
        "certificate_model": "USM001",
        "certificate_valid_until": "2028-02-20",
    }
    reference(erp, pricing=[LINE], certificate_skus=[usm001])
    with erp.connection() as db:
        lines = price_table.index(db)
    assert set(lines.certificates) == {"dg053"}
    info = price_table.for_sku(lines, "dg053.sp")
    assert (info["certificate"], info["certificate_number"], info["model"]) == (
        "Anatel",
        "08215-25-18234",
        "USM001",
    )
    assert info["certificate_valid_until"] == "20/02/2028"
    assert info["pricing_id"] == "l1"
    # Same line, other family (not in the column): the line's own certificate.
    other = price_table.for_sku(lines, "dg052.ra")
    assert (other["certificate_number"], other["model"]) == ("07197-26-18234", "A17 Pro Max")
    assert other["certificate_valid_until"] == "30/07/2028"
    assert other["pricing_id"] == "l1" and other["ean"] == "7908855913182"
    # No certificate anywhere: empty, never "SEM GTIN" or None.
    bare = {
        "dg053": {
            **LINE,
            "certificate": None,
            "certificate_number": None,
            "certificate_model": None,
            "certificate_valid_until": None,
        }
    }
    info = price_table.for_sku(bare, "dg053.sp")
    assert (info["certificate"], info["certificate_number"], info["certificate_valid_until"]) == (
        "",
        "",
        "",
    )


def test_an_old_copy_without_the_new_fields_still_works(erp):
    """The ERP may still hold a copy taken before M-71: no certificate_skus, no end date."""
    old = {k: v for k, v in LINE.items() if k != "certificate_valid_until"}
    reference(erp, pricing=[old])
    with erp.connection() as db:
        lines = price_table.index(db)
    info = price_table.for_sku(lines, "dg053.sp")
    assert info["certificate"] == "Anatel" and info["certificate_valid_until"] == ""
    assert lines.certificates == {}
    # A plain dict (no SKU column at all) is still accepted.
    assert price_table.for_sku({"dg053": old}, "dg053.sp")["certificate_valid_until"] == ""


def test_voltage_and_size_codes_find_their_own_line_and_the_family_certificate(erp):
    """Eletro lines list the full code ("uaf001m1.110, uaf001m1.220"; the pot "uaf001m1.2l"):
    the product finds the line that lists its own code; the certificate of the SKU column goes by
    the family (uaf001m1), so 110 and 220 share it."""
    airfryer = {
        **LINE,
        "id": "l7",
        "sku": "uaf001m1.110, uaf001m1.220",
        "name": "airfryer vidro",
        "department": "eletro",
        "ean": "uaf001m1.110: 7908000000110, uaf001m1.220: 79080",
        "certificate": "inmetro",
        "certificate_number": "MODERNA-0069/25",
        "certificate_model": "UAF001",
        "certificate_valid_until": "2032-04-30",
    }
    pote = {
        **LINE,
        "id": "l8",
        "sku": "uaf001m1.2l",
        "name": "airfryer pote vidro 2,5L",
        "department": "eletro",
        "ean": "",
        "certificate": None,
        "certificate_number": None,
        "certificate_model": None,
        "certificate_valid_until": None,
    }
    reference(erp, pricing=[airfryer, pote])
    with erp.connection() as db:
        lines = price_table.index(db)
    a110 = price_table.for_sku(lines, "uaf001m1.110")
    assert (a110["line"], a110["pricing_id"], a110["ean"]) == (
        airfryer["sku"],
        "l7",
        "7908000000110",
    )
    assert (a110["certificate"], a110["model"], a110["certificate_valid_until"]) == (
        "Inmetro",
        "UAF001",
        "30/04/2032",
    )
    assert price_table.for_sku(lines, "UAF001M1.220")["pricing_id"] == "l7"
    assert price_table.for_sku(lines, "uaf001m1.2l")["pricing_id"] == "l8"
    assert price_table.for_sku(lines, "uaf001m1.2l")["certificate"] == ""
    # With uaf001m1 in the SKU column, both voltages (and the pot, same family) show it.
    uaf = {
        "id": "uaf001m1",
        "sku": "uaf001m1",
        "certificate": "inmetro",
        "certificate_number": "MODERNA-0069/25",
        "certificate_model": "UAF001-M1",
        "certificate_valid_until": "2032-04-30",
    }
    reference(erp, certificate_skus=[uaf])
    with erp.connection() as db:
        lines = price_table.index(db)
    for sku in ("uaf001m1.110", "uaf001m1.220", "uaf001m1.2l"):
        assert price_table.for_sku(lines, sku)["model"] == "UAF001-M1", sku


def test_the_most_specific_line_wins_for_mala_sizes_and_kits(erp):
    """Mala lines list the size ("b005.20") and the kit with accessories; each has its own EAN.
    The product finds its own line (before M-71 it fell back to the "b005" set line and showed
    that EAN)."""
    base = {
        **LINE,
        "department": "mala",
        "certificate": None,
        "certificate_number": None,
        "certificate_model": None,
        "certificate_valid_until": None,
    }
    conjunto = {**base, "id": "m1", "sku": "b005,b006", "ean": "b005: 7908855912949, b006: 1"}
    tam20 = {**base, "id": "m2", "sku": "b005.20,b006.20", "ean": "b005.20: 7908855904050"}
    kit = {
        **base,
        "id": "m3",
        "sku": "b005.20+a075+bp003+a076,b006.20+a075+bp003+a076",
        "ean": "b005.20+a075+bp003+a076: 7908855907730",
    }
    reference(erp, pricing=[conjunto, tam20, kit])
    with erp.connection() as db:
        lines = price_table.index(db)
    assert price_table.for_sku(lines, "b005")["ean"] == "7908855912949"
    assert price_table.for_sku(lines, "B005.20")["ean"] == "7908855904050"
    assert price_table.for_sku(lines, "b005.20+a075+bp003+a076")["ean"] == "7908855907730"
    assert price_table.for_sku(lines, "b005.20+a075+bp003+a076")["pricing_id"] == "m3"
    # A size without its own line falls back to the family, as before.
    assert price_table.for_sku(lines, "b005.24")["pricing_id"] == "m1"
    assert price_table.for_sku(lines, "b005.24")["ean"] == "7908855912949"


def test_certificate_name_shows_anvisa_and_isento_like_the_certificacoes_screen():
    # M-68: Anvisa (só manual) e Isento aparecem com inicial maiúscula, como Anatel e Inmetro.
    for stored, shown in (
        ("anatel", "Anatel"),
        ("inmetro", "Inmetro"),
        ("anvisa", "Anvisa"),
        ("isento", "Isento"),
        (" ANVISA ", "Anvisa"),
        ("", ""),
        (None, ""),
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
