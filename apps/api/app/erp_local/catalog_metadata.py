"""Fill the real workspace with catalogue facts read from the DaVinci mirror.

Runs only on command and never touches stock, prices or anything already edited here. A value
the origin leaves at zero stays unknown, since the origin cannot tell zero from not informed.
"""

import argparse
import json
from decimal import Decimal
from pathlib import Path

from app.erp_local import (
    categories,
    product_details,
    product_structures,
    product_suppliers,
    product_tax,
    rich_text,
)
from app.erp_local.core import ERPError, LocalERP, dumps

STOP = "bling-mirror-v1:"


def _category_id(erp, name, key):
    with erp.connection() as db:
        for row in db.execute("SELECT id,name,parent_id FROM own_categories"):
            if row["parent_id"] is None and row["name"].casefold() == name.casefold():
                return row["id"]
    return erp.command(key, "category.save", {"data": {"name": name}})["id"]


def apply_mirror(erp, rows):
    """Each product is one atomic, replay-safe command; one refusal never stops the others."""
    report = {
        "seen": 0,
        "unknown_sku": 0,
        "already_edited": 0,
        "updated": 0,
        "services": 0,
        "refused": [],
    }
    for row in rows:
        report["seen"] += 1
        sku = row["sku"]
        with erp.connection() as db:
            exists = db.execute("SELECT kind FROM products WHERE sku=?", (sku,)).fetchone()
            if not exists:
                report["unknown_sku"] += 1
                continue
            current = product_details.details(db, sku)
            category = categories.document_snapshot(db, sku)
        if current["version"]:
            report["already_edited"] += 1
            continue
        minimum = Decimal(str(row.get("min_stock") or 0))
        payload = {
            "sku": sku,
            "version": 0,
            "data": {"stock_min": str(minimum)} if minimum > 0 else {},
        }
        if row.get("tipo") == "S" and exists["kind"] == "simple":
            payload["type"] = "service"
        name = (row.get("categoria_nome") or "").strip()
        try:
            if name:
                category_id = _category_id(erp, name, STOP + "category:" + name)
                payload["category"] = {"version": category["version"], "category_id": category_id}
            if not (payload["data"] or "type" in payload or "category" in payload):
                continue
            result = erp.command(STOP + sku, "product.update", payload)
        except ERPError as error:
            report["refused"].append({"sku": sku, "code": error.code})
            continue
        if not result.get("replayed"):
            report["updated"] += 1
            report["services"] += "type" in payload
    return report


IMPORT_PREFIXES = (STOP, "bling-catalog-v1:", "anterior")


def edited_here(db, sku):
    """Another import is not an owner's edit; anything else must be preserved."""
    return any(
        not row[0].startswith(IMPORT_PREFIXES)
        for row in db.execute(
            "SELECT operation_key FROM product_details_history WHERE sku=?", (sku,)
        )
    )


def _positive(value):
    """The origin answers zero when nothing was informed; that stays unknown here."""
    if value in (None, ""):
        return None
    number = Decimal(str(value))
    return str(number) if number > 0 else None


def _to_cm(value, unit):
    number = _positive(value)
    if number is None:
        return None
    factor = {0: Decimal(100), 1: Decimal(1), 2: Decimal("0.1")}.get(unit)
    if factor is None:
        raise ValueError("unidade de medida desconhecida")
    return str((Decimal(number) * factor).quantize(Decimal("0.001")))


def _details_from(raw, skipped):
    data = {}

    def attempt(field, check, value):
        if value in (None, ""):
            return
        try:
            data[field] = check(value)
        except (ERPError, ValueError, ArithmeticError):
            skipped.append(field)

    attempt("unit", lambda v: str(v).strip().upper()[:6], raw.get("unidade"))
    attempt("brand", lambda v: str(v).strip()[:100], raw.get("marca"))
    attempt("gtin", product_details.gtin, raw.get("gtin"))
    attempt("gtin_tax", product_details.gtin, raw.get("gtinEmbalagem"))
    attempt("net_weight_kg", _positive, raw.get("pesoLiquido"))
    attempt("gross_weight_kg", _positive, raw.get("pesoBruto"))
    attempt("items_per_box", _positive, raw.get("itensPorCaixa"))
    if raw.get("volumes"):
        attempt("volumes", int, raw["volumes"])
    dimensions = raw.get("dimensoes") or {}
    unit = dimensions.get("unidadeMedida")
    for source, target in [
        ("largura", "width_cm"),
        ("altura", "height_cm"),
        ("profundidade", "length_cm"),
    ]:
        attempt(target, lambda v, u=unit: _to_cm(v, u), dimensions.get(source))
    if any(dimensions.get(k) for k in ["largura", "altura", "profundidade"]):
        data["dimension_unit"] = {0: "m", 1: "cm", 2: "mm"}.get(unit, "cm")
    if raw.get("tipoProducao") in {"P", "T"}:
        data["production"] = "own" if raw["tipoProducao"] == "P" else "third_party"
    condition = {0: "unspecified", 1: "new", 2: "used", 3: "refurbished"}.get(raw.get("condicao"))
    if condition:
        data["condition"] = condition
    if isinstance(raw.get("freteGratis"), bool):
        data["free_shipping"] = raw["freteGratis"]
    if raw.get("dataValidade") and not str(raw["dataValidade"]).startswith("0000"):
        attempt("expiry_date", product_details._expiry, raw["dataValidade"])
    if raw.get("departamento") in product_details.DEPARTMENTS:
        data["department"] = raw["departamento"]
    attempt("short_description_html", rich_text.clean, raw.get("descricaoCurta"))
    attempt("complementary_html", rich_text.clean, raw.get("descricaoComplementar"))
    attempt("external_link", lambda v: product_details._link(v, "o link"), raw.get("linkExterno"))
    attempt("video_url", lambda v: product_details._link(v, "o vídeo"), raw.get("video_url"))
    attempt("notes", lambda v: str(v).strip()[:5000], raw.get("observacoes"))
    stock = raw.get("estoque") or {}
    attempt("location", lambda v: str(v).strip()[:120], stock.get("localizacao"))
    attempt("stock_min", _positive, stock.get("minimo"))
    attempt("stock_max", _positive, stock.get("maximo"))
    if stock.get("crossdocking"):
        attempt("crossdocking", int, stock["crossdocking"])
    minimum, maximum = data.get("stock_min"), data.get("stock_max")
    if minimum and maximum and Decimal(maximum) < Decimal(minimum):
        del data["stock_max"]
        skipped.append("stock_max")
    return {k: v for k, v in data.items() if v is not None}


def _tax_from(raw, skipped):
    source = raw.get("tributacao") or {}
    fields = {
        "origin": str(source["origem"]) if source.get("origem") is not None else None,
        "ncm": source.get("ncm") or "",
        "cest": source.get("cest") or "",
        "item_type": source.get("spedTipoItem") or "",
        "service_code": source.get("codigoListaServicos") or "",
        "tipi_exception": source.get("codigoExcecaoTipi") or "",
        "additional_info": source.get("dadosAdicionais") or "",
        "approximate_tax_percent": _positive(source.get("percentualTributos")),
        "icms_st_retention_base": _positive(source.get("valorBaseStRetencao")),
        "icms_st_retention_value": _positive(source.get("valorStRetencao")),
        "icms_substitute_value": _positive(source.get("valorICMSSubstituto")),
        "pis_fixed_value": _positive(source.get("valorPisFixo")),
        "cofins_fixed_value": _positive(source.get("valorCofinsFixo")),
    }
    if fields["tipi_exception"] == "0":
        fields["tipi_exception"] = ""
    kept = {}
    for field, value in fields.items():
        try:
            product_tax.normalize({field: value})
        except (ERPError, ValueError, ArithmeticError):
            skipped.append("tax." + field)
            continue
        kept[field] = value
    return kept if kept.get("ncm") or kept.get("service_code") or kept.get("cest") else None


def _load_categories(erp, rows):
    """Keep the origin's ids so products and DaVinci rows keep pointing at the same category."""
    from app.erp_local import bling_compat

    pending, mapping = list(rows), {}
    with erp.connection() as db:
        for row in rows:
            known = bling_compat.local_id(db, "category", row["id"])
            if known:
                mapping[row["id"]] = known
    for _ in range(len(pending) + 1):
        waiting = []
        for row in pending:
            if row["id"] in mapping:
                continue
            parent = row.get("parent_id")
            if parent and parent not in mapping:
                waiting.append(row)
                continue
            parent_id = mapping.get(parent)
            with erp.connection() as db:
                same = db.execute(
                    "SELECT id FROM own_categories WHERE parent_id IS ? AND lower(name)=lower(?)",
                    (parent_id, row["name"]),
                ).fetchone()
            identifier = (
                same["id"]
                if same
                else erp.command(
                    "bling-catalog-v1:category:" + row["id"],
                    "category.save",
                    {"data": {"name": row["name"], "parent_id": parent_id}},
                )["id"]
            )
            with erp.connection() as db:
                db.execute(
                    "INSERT OR IGNORE INTO compat_ids VALUES ('category',?,?)",
                    (identifier, int(row["id"])),
                )
            mapping[row["id"]] = identifier
        pending = waiting
        if not pending:
            break
    return mapping, [row["id"] for row in pending]


def _supplier_row(erp, raw, contacts_cache):
    """Bling answers contact 0 when no supplier was linked; that is not a supplier."""
    source = raw.get("fornecedor") or {}
    contact = source.get("contato") or {}
    if not contact.get("id"):
        return None
    numeric = str(contact["id"])
    if numeric not in contacts_cache:
        contacts_cache[numeric] = erp.command(
            "bling-catalog-v1:contact:" + numeric,
            "contact.save",
            {"data": {"name": contact.get("nome") or numeric, "roles": ["supplier"]}},
        )["id"]
        with erp.connection() as db:
            db.execute(
                "INSERT OR IGNORE INTO compat_ids VALUES ('contact',?,?)",
                (contacts_cache[numeric], int(numeric)),
            )
    return {
        "contact_id": contacts_cache[numeric],
        "description": "",
        "code": str(source.get("codigo") or "")[:60],
        "purchase_price": _positive(source.get("precoCompra")),
        "cost_price": _positive(source.get("precoCusto")),
        "warranty_months": 0,
        "is_default": True,
    }


def apply_bling_read(erp, rows):
    """One replay-safe command per product. Stock, prices and own edits stay untouched."""
    report = {
        "products_read": 0,
        "updated": 0,
        "unknown_sku": 0,
        "edited_here": 0,
        "already_imported": 0,
        "services": 0,
        "with_images_at_origin": 0,
        "refused": [],
        "skipped_fields": {},
        "structure_differences": [],
    }
    categories_read = [r["data"] for r in rows if r["kind"] == "category"]
    mapping, orphans = _load_categories(erp, categories_read)
    report["categories"] = len(mapping)
    report["category_orphans"] = orphans
    contacts_cache = {}
    with erp.connection() as db:
        origin = {}
        for row in db.execute("SELECT sku,data FROM catalog_origin"):
            source = json.loads(row["data"]).get("bling_product_id")
            if source:
                origin[str(source)] = row["sku"]
    for row in rows:
        if row["kind"] != "product":
            continue
        raw = row["data"]
        report["products_read"] += 1
        sku = origin.get(str(raw["id"]))
        if not sku:
            report["unknown_sku"] += 1
            continue
        skipped = []
        data = _details_from(raw, skipped)
        tax = _tax_from(raw, skipped)
        report["with_images_at_origin"] += bool(raw.get("image_paths"))
        with erp.connection() as db:
            product = erp.product(db, sku)
            current = product_details.details(db, sku)
            if db.execute(
                "SELECT 1 FROM events WHERE operation_key=?", ("bling-catalog-v1:" + sku,)
            ).fetchone():
                report["already_imported"] += 1
                continue
            if edited_here(db, sku):
                report["edited_here"] += 1
                continue
            category = categories.document_snapshot(db, sku)
            tax_version = product_tax.details(db, sku)["version"]
            suppliers = product_suppliers.details(db, sku)
            _, recipes = product_structures.graph(db)
        local_parts = {p["sku"]: Decimal(p["quantity"]) for p in recipes.get(sku, [])}
        origin_parts = {
            origin.get(str((p.get("produto") or {}).get("id")), "?"): Decimal(str(p["quantidade"]))
            for p in (raw.get("estrutura") or {}).get("componentes") or []
        }
        if local_parts != origin_parts:
            report["structure_differences"].append(sku)
        payload = {"sku": sku, "version": current["version"], "data": data}
        if raw.get("tipo") == "S" and product["kind"] == "simple":
            payload["type"] = "service"
        if tax and not tax_version:
            payload["tax"] = {"version": 0, "data": tax, "reason": "Cadastro lido do Bling"}
        target = mapping.get(str((raw.get("categoria") or {}).get("id")))
        if target and category["id"] != target:
            payload["category"] = {"version": category["version"], "category_id": target}
        supplier = _supplier_row(erp, raw, contacts_cache)
        if supplier and not suppliers["version"]:
            payload["suppliers"] = {
                "version": 0,
                "rows": [supplier],
                "reason": "Cadastro lido do Bling",
            }
        try:
            result = erp.command("bling-catalog-v1:" + sku, "product.update", payload)
        except ERPError as error:
            report["refused"].append({"sku": sku, "code": error.code})
            continue
        for field in skipped:
            report["skipped_fields"][field] = report["skipped_fields"].get(field, 0) + 1
        if not result.get("replayed"):
            report["updated"] += 1
            report["services"] += "type" in payload
    return report


def convert_virtual_kits(erp, rows):
    """Products Bling composes virtually but that arrived here as simple, with a balance of theirs.

    Their stock comes from the parts, so the old balance goes to zero with its reason on record.
    Local parts are never overwritten: where both sides have parts, a difference is only reported.
    """
    report = {"converted": 0, "balances_cleared": 0, "refused": {}, "conflicts": []}
    with erp.connection() as db:
        origin = {
            str(json.loads(row["data"]).get("bling_product_id")): row["sku"]
            for row in db.execute("SELECT sku,data FROM catalog_origin")
        }
        _, recipes = product_structures.graph(db)
        depots = [row["id"] for row in db.execute("SELECT id FROM deposits")]
    for row in rows:
        if row.get("kind") != "product":
            continue
        raw = row["data"]
        sku = origin.get(str(raw["id"]))
        parts = (raw.get("estrutura") or {}).get("componentes") or []
        if not sku:
            continue
        if recipes.get(sku):
            wanted = {
                origin.get(str((p.get("produto") or {}).get("id"))): Decimal(str(p["quantidade"]))
                for p in parts
            }
            mine = {p["sku"]: Decimal(p["quantity"]) for p in recipes[sku]}
            if wanted != mine:
                report["conflicts"].append(sku)
            continue
        if not parts or (raw.get("estrutura") or {}).get("tipoEstoque") != "V":
            continue
        components = []
        for part in parts:
            part_sku = origin.get(str((part.get("produto") or {}).get("id")))
            if not part_sku:
                report["refused"][sku] = "peça fora do catálogo"
                break
            components.append({"sku": part_sku, "quantity": str(part["quantidade"])})
        if sku in report["refused"]:
            continue
        reason = "Kit virtual no Bling: o saldo vem das peças"
        try:
            for depot in depots:
                with erp.connection() as db:
                    physical, reserved = erp.balance(db, sku, depot)
                if reserved:
                    raise ERPError("kit_reserved", "Produto com reserva.", 409)
                if physical:
                    erp.command(
                        f"catalog-kit-v1:balance:{sku}:{depot}",
                        "stock.post",
                        {
                            "sku": sku,
                            "depot": depot,
                            "type": "B",
                            "quantity": "0",
                            "reason": reason,
                        },
                        actor="carga do cadastro",
                    )
                    report["balances_cleared"] += 1
            with erp.connection() as db:
                version = product_structures.details(db, erp.product(db, sku))["version"]
            erp.command(
                f"catalog-kit-v1:structure:{sku}",
                "product.structure.replace",
                {
                    "sku": sku,
                    "version": version,
                    "components": components,
                    "virtual": True,
                    "reason": reason,
                },
                actor="carga do cadastro",
            )
            report["converted"] += 1
        except ERPError as error:
            report["refused"][sku] = error.code
    return report


def main():
    parser = argparse.ArgumentParser(description="Aplica o cadastro lido, sem tocar a origem")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--rows", type=Path)
    parser.add_argument("--read", type=Path, help="Arquivo da leitura do cadastro pela API")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--convert-virtual-kits", action="store_true")
    args = parser.parse_args()
    erp = LocalERP(args.database)
    if args.read:
        rows = [json.loads(line) for line in args.read.read_text("utf-8").splitlines()]
        report = (
            convert_virtual_kits(erp, rows)
            if args.convert_virtual_kits
            else apply_bling_read(erp, rows)
        )
    else:
        report = apply_mirror(erp, json.loads(args.rows.read_text("utf-8")))
    text = dumps(report)
    if args.report:
        args.report.write_text(text + "\n", "utf-8")
    print(text[:2000])


if __name__ == "__main__":
    main()
