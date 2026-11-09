"""Explicit own price lists. No inferred precedence or marketplace publication."""

import hashlib
import json
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4

from app.erp_local.core import ERPError, dumps, number, text
from app.erp_local.finance import day

SCHEMA = """
CREATE TABLE IF NOT EXISTS own_price_lists (
 id TEXT PRIMARY KEY, version INTEGER NOT NULL, name TEXT NOT NULL,
 status TEXT NOT NULL, data TEXT NOT NULL, updated_at TEXT NOT NULL);
"""
COMMANDS = {"price_list.save"}
CONTEXT_FIELDS = {"date", "contact_id", "payment_method_id", "state", "channel", "store"}


def apply(erp, db, p):
    if (
        not isinstance(p, dict)
        or set(p) - {"id", "version", "data"}
        or not isinstance(p.get("data"), dict)
    ):
        raise ERPError("price_list_fields", "Dados da lista de preços inválidos.")
    raw = p["data"]
    allowed = {
        "name",
        "status",
        "scope",
        "mode",
        "action",
        "value",
        "prices",
        "valid_from",
        "valid_to",
        "notes",
        *CONTEXT_FIELDS - {"date"},
    }
    if set(raw) - allowed:
        raise ERPError("price_list_fields", "Campos da lista de preços inválidos.")
    previous = db.execute("SELECT * FROM own_price_lists WHERE id=?", (p.get("id"),)).fetchone()
    if p.get("id") and not previous:
        raise ERPError("price_list_not_found", "Lista de preços não encontrada.", 404)
    version = previous["version"] if previous else 0
    if type(p.get("version", 0)) is not int or p.get("version", 0) != version:
        raise ERPError("price_list_version", "A lista mudou. Atualize antes de salvar.", 409)
    name = text(raw.get("name"), "nome da lista")
    data = {
        k: raw.get(k, default)
        for k, default in {
            "status": "active",
            "scope": "all",
            "mode": "percent",
            "action": "discount",
        }.items()
    }
    choices = {
        "status": {"active", "inactive"},
        "scope": {"all", "contact", "payment", "state", "store"},
        "mode": {"percent", "value", "custom"},
        "action": {"discount", "surcharge"},
    }
    if any(
        not isinstance(data[k], str) or data[k] not in allowed_values
        for k, allowed_values in choices.items()
    ):
        raise ERPError("price_list_type", "Confira modelo, aplicação e situação da lista.")
    data.update({k: text(raw[k], k) if raw.get(k) else "" for k in CONTEXT_FIELDS - {"date"}})
    for k in ("valid_from", "valid_to"):
        data[k] = day(raw[k]) if raw.get(k) else ""
    if data["valid_from"] and data["valid_to"] and data["valid_from"] > data["valid_to"]:
        raise ERPError("price_list_dates", "A data final deve ser posterior à inicial.")
    if (
        data["scope"] == "contact"
        and not db.execute(
            "SELECT 1 FROM own_contacts WHERE id=? AND status='active'", (data["contact_id"],)
        ).fetchone()
    ):
        raise ERPError("price_list_target", "Selecione um contato próprio ativo.")
    if (
        data["scope"] == "payment"
        and not db.execute(
            "SELECT 1 FROM own_payment_methods WHERE id=? AND status='active'",
            (data["payment_method_id"],),
        ).fetchone()
    ):
        raise ERPError("price_list_target", "Selecione uma forma de pagamento ativa.")
    if data["scope"] == "state":
        from app.erp_local.contacts import STATES

        if data["state"] not in STATES - {"EX"}:
            raise ERPError("price_list_target", "Selecione uma UF válida.")
    if (
        data["scope"] == "store"
        and not db.execute(
            "SELECT 1 FROM store_policies WHERE channel=? AND store=? AND status='shadow'",
            (data["channel"], data["store"]),
        ).fetchone()
    ):
        raise ERPError("price_list_target", "Selecione uma conta identificada.")
    data["value"] = str(number(raw.get("value", "0"), unit=True, zero=True))
    if data["mode"] == "percent" and data["action"] == "discount" and Decimal(data["value"]) > 100:
        raise ERPError("price_list_discount", "Desconto percentual não pode ultrapassar 100%.")
    prices = raw.get("prices", {})
    if (
        not isinstance(prices, dict)
        or len(prices) > 5000
        or data["mode"] == "custom"
        and not prices
    ):
        raise ERPError("price_list_prices", "Informe os preços personalizados por SKU.")
    data["prices"] = {
        erp.product(db, sku)["sku"]: str(number(value, unit=True, zero=True))
        for sku, value in prices.items()
    }
    data["notes"] = text(raw["notes"], "observações", 2000) if raw.get("notes") else ""
    identifier = previous["id"] if previous else str(uuid4())
    db.execute(
        "INSERT INTO own_price_lists VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
        "version=excluded.version,name=excluded.name,status=excluded.status,data=excluded.data,updated_at=excluded.updated_at",
        (identifier, version + 1, name, data["status"], dumps(data), erp.now()),
    )
    return {"id": identifier, "version": version + 1, "external_writes": False}


def snapshot(db):
    return [
        {**dict(r), "data": json.loads(r["data"])}
        for r in db.execute("SELECT * FROM own_price_lists ORDER BY name COLLATE NOCASE,id")
    ]


def context(raw):
    if not isinstance(raw, dict) or set(raw) != CONTEXT_FIELDS:
        raise ERPError("price_context", "Contexto de aplicação da lista inválido.")
    result = {k: text(raw[k], k) if raw[k] else "" for k in CONTEXT_FIELDS - {"date"}}
    result["date"] = day(raw["date"])
    return result


def quote(erp, db, identifier, skus, raw_context):
    ctx = context(raw_context)
    if (
        not isinstance(skus, list)
        or not 1 <= len(skus) <= 200
        or any(not isinstance(s, str) for s in skus)
    ):
        raise ERPError("price_skus", "Informe os SKUs da venda.")
    row = db.execute("SELECT * FROM own_price_lists WHERE id=?", (identifier,)).fetchone()
    if not row:
        raise ERPError("price_list_not_found", "Lista de preços não encontrada.", 404)
    data = json.loads(row["data"])
    if (
        row["status"] != "active"
        or data["valid_from"]
        and ctx["date"] < data["valid_from"]
        or data["valid_to"]
        and ctx["date"] > data["valid_to"]
    ):
        raise ERPError(
            "price_list_unavailable", "Lista inativa ou fora da vigência nesta venda.", 409
        )
    scope_keys = {
        "all": [],
        "contact": ["contact_id"],
        "payment": ["payment_method_id"],
        "state": ["state"],
        "store": ["channel", "store"],
    }[data["scope"]]
    if any(data[k] != ctx[k] for k in scope_keys):
        raise ERPError(
            "price_list_scope",
            "A lista não se aplica ao cliente, pagamento, região ou loja selecionados.",
            409,
        )
    prices, bases = {}, {}
    for sku in sorted(set(skus)):
        erp.product(db, sku)
        price = db.execute("SELECT price FROM product_pricing WHERE sku=?", (sku,)).fetchone()
        base = Decimal(price[0]) if price and price[0] is not None else None
        bases[sku] = str(base) if base is not None else None
        if data["mode"] == "custom":
            if sku not in data["prices"]:
                raise ERPError(
                    "price_list_sku_missing", "SKU sem preço personalizado nesta lista: " + sku, 409
                )
            value = Decimal(data["prices"][sku])
        else:
            if base is None:
                raise ERPError(
                    "price_list_base_missing", "Preço de cadastro pendente para " + sku, 409
                )
            change = (
                base * Decimal(data["value"]) / 100
                if data["mode"] == "percent"
                else Decimal(data["value"])
            )
            value = base + change if data["action"] == "surcharge" else base - change
        if value < 0:
            raise ERPError("price_list_negative", "A lista produz preço negativo para " + sku, 409)
        prices[sku] = str(
            number(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP), unit=True, zero=True)
        )
    frozen = {
        "id": row["id"],
        "version": row["version"],
        "name": row["name"],
        "data": data,
        "context": ctx,
        "base_prices": bases,
        "prices": prices,
    }
    return {**frozen, "token": hashlib.sha256(dumps(frozen).encode()).hexdigest()}


def bind(erp, db, payload, old, items, raw_context):
    identifier = payload.get("price_list_id")
    if not identifier:
        return None
    ctx = context(raw_context)
    previous = old.get("price_quote")
    token = payload.get("price_quote_token")
    if (
        previous
        and previous["id"] == identifier
        and previous["token"] == token
        and previous["context"] == ctx
        and set(previous["prices"]) == {i["sku"] for i in items}
    ):
        frozen = previous
    else:
        frozen = quote(erp, db, identifier, [i["sku"] for i in items], ctx)
        if frozen["token"] != token:
            raise ERPError(
                "price_quote_changed",
                "Aplique a lista novamente antes de salvar; preços ou contexto mudaram.",
                409,
            )
    if any(Decimal(i["unit_price"]) != Decimal(frozen["prices"][i["sku"]]) for i in items):
        raise ERPError(
            "price_quote_mismatch",
            "Os preços diferem da lista. Aplique novamente ou escolha preço manual.",
            409,
        )
    return frozen
