"""Own editable catalogue fields and unambiguous barcode resolution.

GTIN checks format/check digit, not registration with GS1. No remote lookup.
Catalogue cost is distinct from the cost valuation of physical stock.
"""

import json
import re
from datetime import date
from decimal import Decimal
from urllib.parse import urlsplit

from stdnum import ean

from app.erp_local import rich_text
from app.erp_local.core import ERPError, dumps, number, text

SCHEMA = """
CREATE TABLE IF NOT EXISTS product_details (
 sku TEXT PRIMARY KEY REFERENCES products(sku), version INTEGER NOT NULL,
 data TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS product_details_history (
 sku TEXT NOT NULL REFERENCES products(sku), version INTEGER NOT NULL, data TEXT NOT NULL,
 operation_key TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(sku,version));
"""
COMMANDS = {"product.update"}
CONDITIONS = {"unspecified", "new", "used", "refurbished"}
PRODUCTIONS = {"", "own", "third_party"}
DIMENSION_UNITS = {"m", "cm", "mm"}
DEPARTMENTS = {
    "",
    "bebeU",
    "bebeM",
    "bebeF",
    "infanU",
    "infanM",
    "infanF",
    "adultoU",
    "adultoM",
    "adultoF",
}
DETAIL_FIELDS = {
    "unit",
    "gtin",
    "gtin_tax",
    "status",
    "description",
    "brand",
    "location",
    "weight_kg",
    "height_cm",
    "width_cm",
    "length_cm",
    "condition",
    "production",
    "expiry_date",
    "free_shipping",
    "net_weight_kg",
    "gross_weight_kg",
    "volumes",
    "items_per_box",
    "dimension_unit",
    "department",
    "dun",
    "short_description_html",
    "complementary_html",
    "external_link",
    "video_url",
    "notes",
    "tags",
    "stock_min",
    "stock_max",
    "crossdocking",
}


def empty(status="active"):
    """Unknown stays empty: older rows never gain a default the owner did not choose."""
    return {
        "unit": "",
        "gtin": "",
        "gtin_tax": "",
        "status": status,
        "description": "",
        "brand": "",
        "location": "",
        "weight_kg": None,
        "height_cm": None,
        "width_cm": None,
        "length_cm": None,
        "condition": "unspecified",
        "production": "",
        "expiry_date": "",
        "free_shipping": None,
        "net_weight_kg": None,
        "gross_weight_kg": None,
        "volumes": None,
        "items_per_box": None,
        "dimension_unit": "cm",
        "department": "",
        "dun": [],
        "short_description_html": "",
        "complementary_html": "",
        "external_link": "",
        "video_url": "",
        "notes": "",
        "tags": [],
        "stock_min": None,
        "stock_max": None,
        "crossdocking": None,
    }


def gtin(raw):
    if raw in (None, ""):
        return ""
    value = text(raw, "GTIN", 40).upper()
    if value == "SEM GTIN":
        return value
    value = re.sub(r"[\s-]", "", value)
    if not value.isascii() or not value.isdigit() or not ean.is_valid(value):
        raise ERPError("product_gtin", "GTIN inválido. Confira o tamanho e o dígito verificador.")
    return value


def details(db, sku):
    row = db.execute("SELECT version,data FROM product_details WHERE sku=?", (sku,)).fetchone()
    if row:
        data = {**empty(), **json.loads(row["data"])}
        if data["description"] and not data["complementary_html"]:
            data["complementary_html"] = rich_text.from_plain(data["description"])
        return {"version": row["version"], **data}
    origin = db.execute("SELECT data FROM catalog_origin WHERE sku=?", (sku,)).fetchone()
    source_active = not origin or json.loads(origin[0]).get("sellable", True)
    return {"version": 0, **empty("active" if source_active else "inactive")}


def history(db, sku):
    rows = [
        {**dict(r), "data": json.loads(r["data"])}
        for r in db.execute(
            "SELECT version,data,created_at FROM product_details_history "
            "WHERE sku=? ORDER BY version DESC LIMIT 21",
            (sku,),
        )
    ]
    for index, row in enumerate(rows):
        older = rows[index + 1]["data"] if index + 1 < len(rows) else None
        row["changed"] = (
            sorted(k for k in set(row["data"]) | set(older) if row["data"].get(k) != older.get(k))
            if older is not None
            else None
        )
    return rows[:20]


def _link(raw, name):
    if raw in (None, ""):
        return ""
    value = text(raw, name, 500)
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc or re.search(r"\s", value):
        raise ERPError("product_link", f"Informe {name} completo, com http:// ou https://.")
    return value


def _choice(value, choices, code, message):
    if not isinstance(value, str) or value not in choices:
        raise ERPError(code, message)
    return value


def _dun(raw, own_codes):
    if not isinstance(raw, list) or len(raw) > 20:
        raise ERPError("product_dun", "Informe até 20 códigos DUN.")
    rows, seen = [], set()
    for row in raw:
        if not isinstance(row, dict) or set(row) != {"code", "quantity"}:
            raise ERPError("product_dun", "Informe código DUN e quantidade da embalagem.")
        if row["quantity"] in (None, ""):
            raise ERPError("product_dun_quantity", "Informe a quantidade de unidades do DUN.")
        code = re.sub(r"[\s-]", "", text(row["code"], "código DUN", 40))
        if not code.isascii() or len(code) != 14 or not ean.is_valid(code):
            raise ERPError("product_dun", "DUN inválido. Confira os 14 dígitos e o verificador.")
        if code in seen or code in own_codes:
            raise ERPError("product_dun", "Código DUN repetido no cadastro do produto.")
        seen.add(code)
        rows.append({"code": code, "quantity": str(number(row["quantity"]))})
    return rows


def _tags(raw):
    if not isinstance(raw, list) or len(raw) > 50:
        raise ERPError("product_tags", "Informe até 50 tags.")
    tags, seen = [], set()
    for item in raw:
        tag = text(item, "tag", 60)
        if tag.casefold() not in seen:
            seen.add(tag.casefold())
            tags.append(tag)
    return tags


def _expiry(raw):
    if raw in (None, ""):
        return ""
    if not isinstance(raw, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        raise ERPError("product_expiry", "Informe a data de validade do produto.")
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        raise ERPError("product_expiry", "Informe a data de validade do produto.") from None


def _whole(raw, name):
    if raw in (None, ""):
        return None
    if isinstance(raw, bool) or not re.fullmatch(r"\d{1,4}", str(raw)):
        raise ERPError("product_whole", f"{name} precisa ser um número inteiro.")
    return int(raw)


def normalize(raw):
    if not isinstance(raw, dict) or set(raw) - DETAIL_FIELDS - {"name", "price", "cost_price"}:
        raise ERPError("product_fields", "Campos do produto não reconhecidos.")
    data = {}
    for key, label, size in [
        ("unit", "unidade", 6),
        ("brand", "marca", 100),
        ("location", "localização", 120),
        ("notes", "observações", 5000),
    ]:
        data[key] = text(raw[key], label, size) if raw.get(key) else ""
    data["unit"] = data["unit"].upper()
    data.update(gtin=gtin(raw.get("gtin")), gtin_tax=gtin(raw.get("gtin_tax")))
    data["status"] = raw.get("status", "active")
    if data["status"] not in {"active", "inactive"}:
        raise ERPError("product_status", "Escolha produto ativo ou inativo.")
    for key in [
        "weight_kg",
        "net_weight_kg",
        "gross_weight_kg",
        "height_cm",
        "width_cm",
        "length_cm",
        "items_per_box",
        "stock_min",
        "stock_max",
    ]:
        data[key] = str(number(raw[key], zero=True)) if raw.get(key) not in (None, "") else None
    if (
        data["stock_min"] is not None
        and data["stock_max"] is not None
        and Decimal(data["stock_max"]) < Decimal(data["stock_min"])
    ):
        raise ERPError("product_stock_limits", "O estoque máximo não pode ser menor que o mínimo.")
    data["condition"] = _choice(
        raw.get("condition", "unspecified"),
        CONDITIONS,
        "product_condition",
        "Escolha a condição do produto.",
    )
    data["production"] = _choice(
        raw.get("production") or "", PRODUCTIONS, "product_production", "Escolha a produção."
    )
    data["dimension_unit"] = _choice(
        raw.get("dimension_unit", "cm"),
        DIMENSION_UNITS,
        "product_dimension_unit",
        "Escolha a unidade de medida das dimensões.",
    )
    data["department"] = _choice(
        raw.get("department") or "", DEPARTMENTS, "product_department", "Escolha o departamento."
    )
    shipping = raw.get("free_shipping")
    if shipping is not None and not isinstance(shipping, bool):
        raise ERPError("product_free_shipping", "Informe se o frete é grátis.")
    data["free_shipping"] = shipping
    data["expiry_date"] = _expiry(raw.get("expiry_date"))
    data["volumes"] = _whole(raw.get("volumes"), "Volumes")
    data["crossdocking"] = _whole(raw.get("crossdocking"), "Crossdocking")
    data["dun"] = _dun(raw.get("dun", []), {data["gtin"], data["gtin_tax"]})
    data["tags"] = _tags(raw.get("tags", []))
    data["external_link"] = _link(raw.get("external_link"), "o link externo")
    data["video_url"] = _link(raw.get("video_url"), "o link do vídeo")
    data["short_description_html"] = rich_text.clean(
        raw.get("short_description_html"), "descrição curta"
    )
    if raw.get("complementary_html") is None:
        data["description"] = (
            text(raw["description"], "descrição complementar", 2000)
            if raw.get("description")
            else ""
        )
        data["complementary_html"] = rich_text.from_plain(data["description"])
    else:
        data["complementary_html"] = rich_text.clean(
            raw["complementary_html"], "descrição complementar"
        )
        data["description"] = rich_text.plain_text(data["complementary_html"])
    return data


def store(erp, db, key, sku, current, data):
    db.execute(
        "INSERT INTO product_details VALUES (?,?,?,?) ON CONFLICT(sku) DO UPDATE SET "
        "version=excluded.version,data=excluded.data,updated_at=excluded.updated_at",
        (sku, current["version"] + 1, dumps(data), erp.now()),
    )
    if current["version"]:
        previous = {k: v for k, v in current.items() if k != "version"}
        db.execute(
            "INSERT OR IGNORE INTO product_details_history VALUES (?,?,?,?,?)",
            (sku, current["version"], dumps(previous), "anterior", erp.now()),
        )
    db.execute(
        "INSERT INTO product_details_history VALUES (?,?,?,?,?)",
        (sku, current["version"] + 1, dumps(data), key, erp.now()),
    )


def apply(erp, db, key, p):
    from app.erp_local.channels import capture_stock_changes

    if (
        not isinstance(p, dict)
        or not {"sku", "version", "data"} <= set(p)
        or set(p)
        - {"sku", "version", "data", "structure", "suppliers", "tax", "images", "category", "type"}
    ):
        raise ERPError("product_fields", "Informe produto, versão e dados do cadastro.")
    product = erp.product(db, p["sku"])
    current = details(db, product["sku"])
    if isinstance(p["version"], bool) or p["version"] != current["version"]:
        raise ERPError("product_conflict", "O produto mudou. Atualize antes de salvar.", 409)
    if current["status"] == "deleted":
        raise ERPError("product_deleted", "Restaure o produto antes de editar.", 409)
    raw = p["data"]
    if not isinstance(raw, dict):
        raise ERPError("product_fields", "Dados do produto não reconhecidos.")
    merged = {**{field: current[field] for field in DETAIL_FIELDS}, **raw}
    if "description" in raw and "complementary_html" not in raw:
        merged["complementary_html"] = None
    data = normalize(merged)
    if "description" not in raw and data["complementary_html"] == current["complementary_html"]:
        data["description"] = current["description"]
    name = text(raw.get("name", product["name"]), "nome")
    price = db.execute(
        "SELECT price,cost_price FROM product_pricing WHERE sku=?", (product["sku"],)
    ).fetchone()
    prices = []
    for index, field in enumerate(["price", "cost_price"]):
        prices.append(
            str(number(raw[field], unit=True, zero=True))
            if raw.get(field) not in (None, "")
            else price[index]
            if price
            else None
        )
    if "structure" in p:
        from app.erp_local import product_structures

        raw_structure = p["structure"]
        if not isinstance(raw_structure, dict) or set(raw_structure) != {
            "version",
            "components",
            "reason",
        }:
            raise ERPError("structure_fields", "Informe versão, componentes e motivo da estrutura.")
        product_structures.apply(erp, db, key, {**raw_structure, "sku": product["sku"]})
    if "suppliers" in p:
        from app.erp_local import product_suppliers

        raw_suppliers = p["suppliers"]
        if not isinstance(raw_suppliers, dict) or set(raw_suppliers) != {
            "version",
            "rows",
            "reason",
        }:
            raise ERPError("supplier_fields", "Informe versão, fornecedores e motivo.")
        product_suppliers.apply(erp, db, key, {"sku": product["sku"], **raw_suppliers})
    if "tax" in p:
        from app.erp_local import product_tax

        raw_tax = p["tax"]
        if not isinstance(raw_tax, dict) or set(raw_tax) != {"version", "data", "reason"}:
            raise ERPError("product_tax_fields", "Informe versão, dados fiscais e motivo.")
        product_tax.apply(erp, db, key, {"sku": product["sku"], **raw_tax})
    if "images" in p:
        from app.erp_local import product_images

        raw_images = p["images"]
        if not isinstance(raw_images, dict) or set(raw_images) != {"version", "rows", "reason"}:
            raise ERPError("image_fields", "Informe versão, imagens e motivo.")
        product_images.apply(
            erp, db, key, "product.images.save", {"sku": product["sku"], **raw_images}
        )
    if "type" in p:
        from app.erp_local import product_actions

        product_actions.set_type(erp, db, product, p["type"])
    if "category" in p:
        from app.erp_local import categories

        raw_category = p["category"]
        if not isinstance(raw_category, dict) or set(raw_category) != {"version", "category_id"}:
            raise ERPError("category_fields", "Informe categoria e versão do vínculo.")
        categories.apply(
            erp, db, "category.assign", {"items": [{"sku": product["sku"], **raw_category}]}
        )
    db.execute("UPDATE products SET name=? WHERE sku=?", (name, product["sku"]))
    db.execute(
        "INSERT INTO product_pricing VALUES (?,?,?) ON CONFLICT(sku) DO UPDATE SET "
        "price=excluded.price,cost_price=excluded.cost_price",
        (product["sku"], *prices),
    )
    store(erp, db, key, product["sku"], current, data)
    if current["status"] != data["status"]:
        capture_stock_changes(erp, db, key, changed_skus={product["sku"]})
    return {"sku": product["sku"], "version": current["version"] + 1, "external_writes": False}


def resolve_reading(db, identifier, *, identifier_type="auto", allowed=None):
    """Return the product and the units one read stands for; a box code is never one unit."""
    value = text(identifier, "SKU ou GTIN")
    if identifier_type not in {"auto", "sku"}:
        raise ERPError("product_identifier", "Tipo de leitura não reconhecido.")
    candidates = {
        r[0]: Decimal(1) for r in db.execute("SELECT sku FROM products WHERE sku=?", (value,))
    }
    if identifier_type == "auto" and value.isascii() and value.isdigit():
        for row in db.execute("SELECT sku,data FROM product_details"):
            data = json.loads(row["data"])
            if value in {data["gtin"], data["gtin_tax"]}:
                candidates[row["sku"]] = Decimal(1)
            for box in data.get("dun", []):
                if box["code"] == value:
                    candidates[row["sku"]] = Decimal(box["quantity"])
    if allowed is not None:
        candidates = {sku: units for sku, units in candidates.items() if sku in set(allowed)}
    if len(candidates) > 1:
        raise ERPError(
            "product_identifier_ambiguous",
            "Código corresponde a mais de um produto. Selecione leitura por SKU.",
            409,
        )
    if not candidates:
        raise ERPError("product_not_found", "SKU ou GTIN não encontrado no cadastro próprio.", 404)
    return next(iter(candidates.items()))


def document_snapshot(erp, db, sku):
    from app.erp_local import product_structures, product_tax
    from app.erp_local.categories import document_snapshot as category_snapshot

    product, extra = erp.product(db, sku), details(db, sku)
    return {
        "sku": sku,
        "name": product["name"],
        "category": category_snapshot(db, sku),
        "structure": product_structures.details(db, product),
        "tax": product_tax.details(db, sku),
        **{
            k: extra[k]
            for k in [
                "version",
                "unit",
                "gtin",
                "gtin_tax",
                "brand",
                "weight_kg",
                "height_cm",
                "width_cm",
                "length_cm",
                "dimension_unit",
                "net_weight_kg",
                "gross_weight_kg",
                "volumes",
                "items_per_box",
                "condition",
                "production",
                "free_shipping",
            ]
        },
        **({"type": "service"} if product["kind"] == "service" else {}),
    }
