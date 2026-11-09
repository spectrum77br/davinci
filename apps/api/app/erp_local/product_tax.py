"""Versioned product tax registration, not a tax calculation or NF-e issuer.

Mirrors the product fields observed in the Bling form. Syntactic validation
does not verify current NCM/CEST tables, regime, rates, tax treatment or reform
rules. Unspecified values stay unknown and are never defaulted to national/0.
"""

import json
import re
from decimal import Decimal

from app.erp_local.core import ERPError, dumps, number, text

SCHEMA = """
CREATE TABLE IF NOT EXISTS product_tax (
 sku TEXT PRIMARY KEY REFERENCES products(sku), version INTEGER NOT NULL,
 data TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS product_tax_history (
 sku TEXT REFERENCES products(sku), version INTEGER NOT NULL, data TEXT NOT NULL,
 reason TEXT NOT NULL, operation_key TEXT REFERENCES events(operation_key), created_at TEXT,
 PRIMARY KEY(sku,version));
"""
COMMANDS = {"product.tax.save"}
ITEM_TYPES = {"00", "01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "99"}
AMOUNTS = {
    "approximate_tax_percent",
    "icms_st_retention_base",
    "icms_st_retention_value",
    "icms_substitute_value",
    "pis_fixed_value",
    "cofins_fixed_value",
}
FIELDS = AMOUNTS | {
    "origin",
    "ncm",
    "cest",
    "item_type",
    "product_group",
    "tipi_exception",
    "additional_info",
    "service_code",
}


def empty():
    return {
        **dict.fromkeys(AMOUNTS),
        "origin": None,
        "ncm": "",
        "cest": "",
        "item_type": "",
        "product_group": "",
        "tipi_exception": "",
        "additional_info": "",
        "service_code": "",
    }


def normalize(raw):
    if not isinstance(raw, dict) or set(raw) - FIELDS:
        raise ERPError("product_tax_fields", "Campos do cadastro fiscal não reconhecidos.")
    data = {**empty(), **raw}
    origin = data["origin"]
    if origin not in (None, "") and (type(origin) is not str or origin not in set("012345678")):
        raise ERPError("product_tax_origin", "Escolha a origem informada para o produto.")
    data["origin"] = origin if origin != "" else None
    if not isinstance(data["item_type"], str) or data["item_type"] not in ITEM_TYPES | {""}:
        raise ERPError("product_tax_item", "Escolha o tipo do item informado.")
    for key, sizes in [("ncm", {2, 8}), ("cest", {7}), ("tipi_exception", {1, 2, 3})]:
        value = data[key]
        if not isinstance(value, str) or len(value) > 16:
            raise ERPError("product_tax_code", "Código fiscal precisa ser texto.")
        value = re.sub(r"[.\s]", "", value)
        if value and (not re.fullmatch(r"[0-9]+", value) or len(value) not in sizes):
            raise ERPError("product_tax_code", "Confira o formato de NCM, CEST e exceção TIPI.")
        data[key] = value
    service = data["service_code"]
    if not isinstance(service, str) or not re.fullmatch(r"[0-9.]{0,10}", service):
        raise ERPError("product_tax_code", "Confira o código da lista de serviços.")
    for key, limit in [("product_group", 120), ("additional_info", 500)]:
        if not isinstance(data[key], str):
            raise ERPError(
                "product_tax_text", "Grupo e informações adicionais precisam ser textos."
            )
        data[key] = text(data[key], key, limit) if data[key] else ""
    for key in AMOUNTS:
        data[key] = (
            str(number(data[key], unit=True, zero=True)) if data[key] not in (None, "") else None
        )
    if (
        data["approximate_tax_percent"] is not None
        and Decimal(data["approximate_tax_percent"]) > 100
    ):
        raise ERPError("product_tax_percent", "Percentual informado ultrapassa cem por cento.")
    return data


def details(db, sku):
    row = db.execute("SELECT version,data FROM product_tax WHERE sku=?", (sku,)).fetchone()
    return (
        {"version": row["version"], "data": json.loads(row["data"])}
        if row
        else {"version": 0, "data": empty()}
    )


def snapshot(db):
    return {
        r["sku"]: {"version": r["version"], "data": json.loads(r["data"])}
        for r in db.execute("SELECT * FROM product_tax")
    }


def history(db, sku):
    return [
        {**dict(r), "data": json.loads(r["data"])}
        for r in db.execute(
            "SELECT version,data,reason,created_at FROM product_tax_history "
            "WHERE sku=? ORDER BY version DESC LIMIT 20",
            (sku,),
        )
    ]


def apply(erp, db, key, payload):
    if not isinstance(payload, dict) or set(payload) != {"sku", "version", "data", "reason"}:
        raise ERPError("product_tax_fields", "Informe produto, versão, dados fiscais e motivo.")
    sku = erp.product(db, payload["sku"])["sku"]
    current = details(db, sku)
    if type(payload["version"]) is not int or payload["version"] != current["version"]:
        raise ERPError("product_tax_version", "O cadastro fiscal mudou. Atualize a tela.", 409)
    if not isinstance(payload["data"], dict):
        raise ERPError("product_tax_fields", "Dados fiscais não reconhecidos.")
    data = normalize({**current["data"], **payload["data"]})
    reason = text(payload["reason"], "motivo do cadastro fiscal", 500)
    version = current["version"] + 1
    db.execute(
        "INSERT INTO product_tax VALUES (?,?,?,?) ON CONFLICT(sku) DO UPDATE SET "
        "version=excluded.version,data=excluded.data,updated_at=excluded.updated_at",
        (sku, version, dumps(data), erp.now()),
    )
    db.execute(
        "INSERT INTO product_tax_history VALUES (?,?,?,?,?,?)",
        (sku, version, dumps(data), reason, key, erp.now()),
    )
    return {
        "sku": sku,
        "tax_version": version,
        "classification_verified": False,
        "external_writes": False,
    }
