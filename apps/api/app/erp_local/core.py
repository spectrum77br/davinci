"""Transactional local ERP, with no settings, credentials or network imports.

SQLite is a development adapter. The production Bling mirror is never the
target of these commands. Quantities and money are stored as decimal strings.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4


class ERPError(ValueError):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def number(value, *, money=False, zero=False, unit=False) -> Decimal:
    try:
        n = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ERPError("invalid_number", "Informe um número válido.") from None
    precision = Decimal("0.0001" if unit else "0.01" if money else "0.001")
    if not n.is_finite() or abs(n) > Decimal("9999999"):
        raise ERPError("invalid_number", "Número fora do limite local.")
    if n < 0 or (not zero and n == 0):
        raise ERPError("invalid_number", "O valor deve ser positivo.")
    if n != n.quantize(precision):
        raise ERPError("invalid_precision", "Use até 4 casas em preço unitário e 3 em quantidade.")
    return n.quantize(precision)


def text(value, name: str, limit=120) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ERPError("invalid_field", f"Preencha {name} (até {limit} caracteres).")
    return value.strip()


def dumps(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def total(items) -> str:
    amount = sum((Decimal(i["quantity"]) * Decimal(i["unit_price"]) for i in items), Decimal(0))
    return str(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


SCHEMA = """
CREATE TABLE IF NOT EXISTS erp_local_meta (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS products (
 sku TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, components TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS deposits (id TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS balances (
 sku TEXT REFERENCES products(sku), depot TEXT REFERENCES deposits(id),
 physical TEXT NOT NULL, reserved TEXT NOT NULL, PRIMARY KEY(sku,depot));
CREATE TABLE IF NOT EXISTS events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, operation_key TEXT UNIQUE NOT NULL,
 fingerprint TEXT NOT NULL, kind TEXT NOT NULL, actor TEXT NOT NULL,
 payload TEXT NOT NULL, result TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS movements (
 id INTEGER PRIMARY KEY AUTOINCREMENT, operation_key TEXT REFERENCES events(operation_key),
 sku TEXT REFERENCES products(sku), depot TEXT REFERENCES deposits(id),
 physical_delta TEXT NOT NULL, reserved_delta TEXT NOT NULL, reason TEXT NOT NULL,
 unit_cost TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS documents (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, external_key TEXT UNIQUE,
 status TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS accounts (
 id TEXT PRIMARY KEY, document_id TEXT REFERENCES documents(id),
 kind TEXT NOT NULL, amount TEXT NOT NULL, status TEXT NOT NULL,
 UNIQUE(document_id,kind));
CREATE TABLE IF NOT EXISTS catalog_origin (
 sku TEXT PRIMARY KEY REFERENCES products(sku), data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS catalog_import (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS product_pricing (
 sku TEXT PRIMARY KEY REFERENCES products(sku), price TEXT, cost_price TEXT);
CREATE TABLE IF NOT EXISTS channel_inbox (
 id INTEGER PRIMARY KEY AUTOINCREMENT, channel TEXT NOT NULL, store TEXT NOT NULL,
 event_id TEXT NOT NULL, external_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
 payload TEXT NOT NULL, source TEXT NOT NULL, status TEXT NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0, outcome TEXT, received_at TEXT NOT NULL,
 UNIQUE(channel,store,event_id));
CREATE TABLE IF NOT EXISTS channel_watermarks (
 channel TEXT, store TEXT, external_id TEXT, occurred_at TEXT NOT NULL,
 state TEXT NOT NULL, PRIMARY KEY(channel,store,external_id));
CREATE TABLE IF NOT EXISTS stock_routes (
 id TEXT PRIMARY KEY, sku TEXT REFERENCES products(sku), channel TEXT NOT NULL,
 store TEXT NOT NULL, store_name TEXT NOT NULL, external_id TEXT NOT NULL,
 variation_id TEXT, depot TEXT REFERENCES deposits(id), active INTEGER NOT NULL,
 source_digest TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS stock_routes_sku ON stock_routes(sku,active);
CREATE TABLE IF NOT EXISTS stock_intents (
 id INTEGER PRIMARY KEY AUTOINCREMENT, route_id TEXT REFERENCES stock_routes(id),
 operation_key TEXT REFERENCES events(operation_key), quantity TEXT,
 status TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS stock_intents_route ON stock_intents(route_id,status);
"""


class LocalERP:
    def __init__(self, database: str | Path):
        from app.erp_local import (
            access,
            bank_statements,
            bling_compat,
            cash_refunds,
            cash_register,
            categories,
            contacts,
            deposits,
            finance,
            fiscal,
            fiscal_invoices,
            inbound_invoices,
            inbound_sync,
            integration_registry,
            inventory,
            logistics,
            multi_company,
            native_ingest,
            native_orders,
            payment_methods,
            pos,
            price_lists,
            procurement,
            product_details,
            product_images,
            product_structures,
            product_suppliers,
            product_tax,
            production,
            proposals,
            recipient_capture,
            returns,
            sellers,
            stock_allocation,
            stock_priority,
            store_labels,
        )

        self.database = Path(database).resolve()
        if self.database.suffix != ".sqlite3":
            raise ERPError("local_database_required", "Use um arquivo local .sqlite3.")
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and "erp_local_meta" not in tables:
                raise ERPError("foreign_database", "Este arquivo não é um banco do ERP local.")
            db.executescript(
                SCHEMA
                + cash_register.SCHEMA
                + payment_methods.SCHEMA
                + deposits.SCHEMA
                + categories.SCHEMA
                + contacts.SCHEMA
                + sellers.SCHEMA
                + product_details.SCHEMA
                + bling_compat.SCHEMA
                + product_images.SCHEMA
                + product_structures.SCHEMA
                + product_suppliers.SCHEMA
                + product_tax.SCHEMA
                + inventory.SCHEMA
                + finance.SCHEMA
                + cash_refunds.SCHEMA
                + pos.SCHEMA
                + returns.SCHEMA
                + production.SCHEMA
                + proposals.SCHEMA
                + price_lists.SCHEMA
                + procurement.SCHEMA
                + fiscal.SCHEMA
                + fiscal_invoices.SCHEMA
                + multi_company.SCHEMA
                + native_ingest.SCHEMA
                + recipient_capture.SCHEMA
                + access.SCHEMA
                + inbound_invoices.SCHEMA
                + inbound_sync.SCHEMA
                + store_labels.SCHEMA
                + bank_statements.SCHEMA
                + stock_priority.SCHEMA
                + logistics.SCHEMA
                + integration_registry.SCHEMA
                + native_orders.SCHEMA
                + stock_allocation.SCHEMA
            )
            version = db.execute("SELECT version FROM erp_local_meta").fetchone()
            if version and version[0] != 1:
                raise ERPError("schema_version", "Versão de banco local incompatível.")
            if not version:
                db.execute("INSERT INTO erp_local_meta VALUES (1)")
            db.execute("BEGIN IMMEDIATE")
            finance.ensure_schedules(db)
            db.commit()

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.database, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        try:
            yield db
        finally:
            db.close()

    def command(self, operation_key: str, kind: str, payload: dict, actor="operador local") -> dict:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                result = self.execute_in(db, operation_key, kind, payload, actor)
                db.commit()
                return result
            except Exception:
                db.rollback()
                raise

    def execute_in(self, db, operation_key, kind, payload, actor="operador local"):
        """Run inside the caller's transaction: event, movements and intents are atomic."""
        from app.erp_local.channels import capture_stock_changes

        operation_key = text(operation_key, "identificador da operação")
        actor = text(actor, "operador")
        fingerprint = hashlib.sha256(dumps([kind, payload, actor]).encode()).hexdigest()
        previous = db.execute(
            "SELECT fingerprint,result FROM events WHERE operation_key=?", (operation_key,)
        ).fetchone()
        if previous:
            if previous["fingerprint"] != fingerprint:
                raise ERPError(
                    "idempotency_conflict", "Essa operação já foi usada com outros dados.", 409
                )
            return {**json.loads(previous["result"]), "replayed": True}
        db.execute(
            "INSERT INTO events(operation_key,fingerprint,kind,actor,payload,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (operation_key, fingerprint, kind, actor, dumps(payload), self.now()),
        )
        result = self.apply(db, operation_key, kind, payload)
        from app.erp_local.finance import ensure_schedules
        from app.erp_local.sellers import ensure_accounts

        ensure_accounts(self, db)
        ensure_schedules(db)
        capture_stock_changes(self, db, operation_key)
        from app.erp_local.bling_compat import capture_events, capture_stock_events

        capture_events(self, db, kind, payload, result)
        capture_stock_events(self, db, operation_key)
        db.execute(
            "UPDATE events SET result=? WHERE operation_key=?", (dumps(result), operation_key)
        )
        return {**result, "replayed": False}

    @staticmethod
    def now():
        return datetime.now(UTC).isoformat()

    @staticmethod
    def product(db, sku):
        sku = text(sku, "SKU")
        row = db.execute("SELECT * FROM products WHERE sku=?", (sku,)).fetchone()
        if not row:
            raise ERPError("product_not_found", "Produto não encontrado.", 404)
        return dict(row)

    @staticmethod
    def origin(db, sku):
        row = db.execute("SELECT data FROM catalog_origin WHERE sku=?", (sku,)).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def depot(db, depot):
        depot = text(depot, "depósito")
        if not db.execute("SELECT 1 FROM deposits WHERE id=?", (depot,)).fetchone():
            raise ERPError("depot_not_found", "Depósito não encontrado.", 404)
        return depot

    @staticmethod
    def balance(db, sku, depot):
        row = db.execute(
            "SELECT physical,reserved FROM balances WHERE sku=? AND depot=?", (sku, depot)
        ).fetchone()
        return (Decimal(row[0]), Decimal(row[1])) if row else (Decimal(0), Decimal(0))

    def move(self, db, key, sku, depot, physical, reserved, reason, unit_cost=None):
        unit_cost = str(unit_cost) if unit_cost is not None else None
        self.depot(db, depot)
        kind = self.product(db, sku)["kind"]
        if kind == "service":
            raise ERPError("service_stock", "Serviço não movimenta estoque: " + sku)
        if kind != "simple":
            raise ERPError("virtual_kit", "Movimente os componentes físicos do kit.")
        origin = self.origin(db, sku)
        if origin and origin["catalog_state"] != "ready":
            raise ERPError(
                "catalog_review", "Confira a identidade do produto antes de movimentar " + sku, 409
            )
        if origin and origin["stock_state"] != "known":
            raise ERPError(
                "stock_review", "Saldo de abertura pendente de conferência para " + sku, 409
            )
        old_physical, old_reserved = self.balance(db, sku, depot)
        new_physical, new_reserved = old_physical + physical, old_reserved + reserved
        if (
            new_physical < min(Decimal(0), old_physical)
            or new_reserved < 0
            or new_physical - new_reserved < min(Decimal(0), old_physical - old_reserved)
        ):
            raise ERPError("insufficient_stock", f"Saldo livre insuficiente para {sku}.", 409)
        db.execute(
            "INSERT INTO balances VALUES (?,?,?,?) ON CONFLICT(sku,depot) DO UPDATE "
            "SET physical=excluded.physical,reserved=excluded.reserved",
            (sku, depot, str(new_physical), str(new_reserved)),
        )
        db.execute(
            "INSERT INTO movements(operation_key,sku,depot,physical_delta,reserved_delta,"
            "reason,unit_cost,created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (key, sku, depot, str(physical), str(reserved), reason, unit_cost, self.now()),
        )
        from app.erp_local.procurement import track_cost

        track_cost(self, db, key, sku, depot, old_physical, physical, unit_cost)

    def items(self, db, raw, *, simple_only=False, for_components=False, historical=False):
        if not isinstance(raw, list) or not raw or len(raw) > 200:
            raise ERPError("invalid_items", "Informe de 1 a 200 itens.")
        result = []
        for item in raw:
            if not isinstance(item, dict):
                raise ERPError("invalid_items", "Item inválido.")
            sku = text(item.get("sku"), "SKU")
            product = self.product(db, sku)
            origin = self.origin(db, sku)
            from app.erp_local.product_details import details

            own_details = details(db, sku)
            if (
                not historical
                and not simple_only
                and not for_components
                and own_details["status"] != "active"
            ):
                raise ERPError("product_inactive", "Produto inativo para novas vendas: " + sku, 409)
            if (
                not historical
                and origin
                and (
                    origin["catalog_state"] != "ready"
                    or not origin["sellable"]
                    and own_details["version"] == 0
                    and not (simple_only or for_components)
                )
            ):
                raise ERPError("catalog_review", "Produto pendente de conferência: " + sku, 409)
            if simple_only and product["kind"] != "simple":
                raise ERPError(
                    "physical_items_only", "Compra recebe produtos físicos, não kits virtuais."
                )
            result.append(
                {
                    "sku": sku,
                    "quantity": str(number(item.get("quantity"))),
                    "unit_price": str(
                        number(item.get("unit_price", "0"), money=True, unit=True, zero=True)
                    ),
                }
            )
        return result

    def requirements(self, db, items):
        result = {}
        for item in items:
            p = self.product(db, item["sku"])
            if p["kind"] == "service":
                continue
            parts = (
                json.loads(p["components"])
                if p["kind"] == "kit"
                else [{"sku": p["sku"], "quantity": "1"}]
            )
            for part in parts:
                sku = part["sku"]
                result[sku] = result.get(sku, Decimal(0)) + Decimal(part["quantity"]) * Decimal(
                    item["quantity"]
                )
        return {k: str(v) for k, v in sorted(result.items())}

    @staticmethod
    def document(db, document_id, kind):
        document_id = text(document_id, "documento")
        row = db.execute(
            "SELECT * FROM documents WHERE id=? AND kind=?", (document_id, kind)
        ).fetchone()
        if not row:
            raise ERPError("document_not_found", "Documento não encontrado.", 404)
        return dict(row), json.loads(row["data"])

    @staticmethod
    def update_document(db, document_id, status, data):
        db.execute(
            "UPDATE documents SET status=?,data=? WHERE id=?", (status, dumps(data), document_id)
        )

    @staticmethod
    def account(db, document_id, kind, amount):
        previous = db.execute(
            "SELECT * FROM accounts WHERE document_id=? AND kind=?", (document_id, kind)
        ).fetchone()
        if previous:
            if Decimal(previous["amount"]) != Decimal(amount):
                raise ERPError(
                    "account_conflict",
                    "A conta existente possui outro valor; concilie antes de relançar.",
                    409,
                )
            return previous["id"]
        identifier = str(uuid4())
        db.execute(
            "INSERT INTO accounts VALUES (?,?,?,?,?)",
            (identifier, document_id, kind, amount, "open"),
        )
        return identifier

    def apply(self, db, key, kind, p, *, _logistics=False, _pos=False):
        from app.erp_local import (
            cash_register,
            categories,
            contacts,
            deposits,
            finance,
            fiscal,
            fiscal_invoices,
            integration_registry,
            inventory,
            logistics,
            native_orders,
            payment_methods,
            pos,
            price_lists,
            procurement,
            product_details,
            product_structures,
            production,
            proposals,
            returns,
            sales,
            sellers,
            stock_allocation,
        )

        if kind in payment_methods.COMMANDS:
            return payment_methods.apply(self, db, p)

        if kind == "order.compat_situation":
            from app.erp_local.bling_compat import apply_situation

            return apply_situation(self, db, key, p)

        if kind == "catalog.follow.add":
            from app.erp_local import catalog_follow

            return catalog_follow.apply(self, db, key, kind, p)

        if kind in {"order.occurrence", "order.occurrence.remove"}:
            from app.erp_local.bling_compat import apply_occurrence, remove_occurrence

            run = apply_occurrence if kind == "order.occurrence" else remove_occurrence
            return run(self, db, key, p)

        if kind == "ingest.account.set":
            from app.erp_local import native_ingest

            return native_ingest.apply(self, db, p)

        if kind in {"access.user.save", "access.profile.save"}:
            from app.erp_local import access

            return access.apply(self, db, kind, p)

        if kind in {"inbound.import", "inbound.link", "inbound.book", "inbound.reject"}:
            from app.erp_local import inbound_invoices

            return inbound_invoices.apply(self, db, key, kind, p)

        if kind in {
            "fiscal.candidates.load",
            "fiscal.technical.save",
            "fiscal.company.adopt",
            "fiscal.route.save",
        }:
            from app.erp_local import multi_company

            return multi_company.apply(self, db, kind, p)

        if kind in {"inbound.dfe.record", "inbound.manifest.record"}:
            from app.erp_local import inbound_sync

            return inbound_sync.apply(self, db, kind, p)

        if kind == "bank.statement.import":
            from app.erp_local import bank_statements

            return bank_statements.apply(self, db, p)

        if kind == "recipient.capture":
            from app.erp_local import recipient_capture

            return recipient_capture.apply(self, db, p)

        if kind in native_orders.COMMANDS:
            return native_orders.apply(self, db, p)

        if kind in cash_register.COMMANDS:
            actor = db.execute("SELECT actor FROM events WHERE operation_key=?", (key,)).fetchone()[
                0
            ]
            return cash_register.apply(self, db, kind, p, actor)

        if kind in pos.COMMANDS:
            actor = db.execute("SELECT actor FROM events WHERE operation_key=?", (key,)).fetchone()[
                0
            ]
            return pos.apply(self, db, key, kind, p, actor)

        if kind in production.COMMANDS:
            return production.apply(self, db, key, kind, p)

        if kind in proposals.COMMANDS:
            return proposals.apply(self, db, kind, p)

        if kind in categories.COMMANDS:
            return categories.apply(self, db, kind, p)

        if kind in price_lists.COMMANDS:
            return price_lists.apply(self, db, p)

        if kind in sellers.COMMANDS:
            return sellers.apply(self, db, p)

        if kind in deposits.COMMANDS:
            return deposits.apply(self, db, key, p)

        if kind in returns.COMMANDS:
            return returns.apply(self, db, key, kind, p)

        if kind in sales.COMMANDS:
            return sales.apply(self, db, key, kind, p)

        if kind in stock_allocation.COMMANDS:
            return stock_allocation.apply(self, db, key, p)

        if kind in product_details.COMMANDS:
            return product_details.apply(self, db, key, p)

        if kind in {"stock.priority.rule", "order.stock_priority"}:
            from app.erp_local import stock_priority

            return stock_priority.apply(self, db, key, kind, p)

        if kind == "order.notes":
            from app.erp_local.bling_compat import notes_command

            return notes_command(self, db, p)

        if kind.startswith("product."):
            from app.erp_local import product_actions

            if kind in product_actions.COMMANDS:
                return product_actions.apply(self, db, key, kind, p)

        if kind == "product.suppliers.save":
            from app.erp_local import product_suppliers

            return product_suppliers.apply(self, db, key, p)

        if kind == "product.tax.save":
            from app.erp_local import product_tax

            return product_tax.apply(self, db, key, p)

        if kind in {"catalog.image.upload", "product.images.save"}:
            from app.erp_local import product_images

            return product_images.apply(self, db, key, kind, p)

        if kind in product_structures.COMMANDS:
            return product_structures.apply(self, db, key, p)

        if kind in contacts.COMMANDS:
            return contacts.apply(self, db, p)

        if kind in inventory.COMMANDS:
            return inventory.apply(self, db, key, kind, p)

        if kind in integration_registry.COMMANDS:
            return integration_registry.apply(self, db, p)

        if kind in logistics.COMMANDS:
            return logistics.apply(self, db, key, kind, p)

        if kind == "fiscal.xml.import":
            return fiscal.apply(self, db, p)
        if kind in fiscal_invoices.COMMANDS:
            return fiscal_invoices.apply(self, db, key, kind, p)

        if kind in finance.COMMANDS:
            return finance.apply(self, db, key, kind, p)
        if kind in procurement.COMMANDS:
            return procurement.apply(self, db, key, kind, p)
        allowed = {
            "demo.seed": set(),
            "depot.create": {"id", "name"},
            "product.create": {
                "sku",
                "name",
                "kind",
                "components",
                "price",
                "cost_price",
                *product_details.DETAIL_FIELDS,
            },
            "stock.post": {
                "sku",
                "depot",
                "type",
                "quantity",
                "reason",
                "destination",
                "unit_cost",
                "purchase_price",
            },
            "stock.transfer": {"sku", "depot", "type", "quantity", "reason", "destination"},
            "order.import": {
                "channel",
                "store",
                "external_id",
                "depot",
                "items",
                "customer_name",
                "contact_id",
                "date",
                "expected_date",
                "recipient",
                "channel_fees",
                "purchase_order",
                "fulfillment_mode",
                "discount",
                "freight",
            },
            "order.cancel": {"id"},
            "order.ship": {"id"},
            "order.return": {"id", "destination", "condition"},
            "purchase.create": {"supplier", "depot", "items"},
            "purchase.stock": {"id"},
            "purchase.accounts": {"id"},
            "purchase.reverse_stock": {"id"},
            "purchase.reverse_accounts": {"id"},
            "account.settle": {"id"},
        }
        if kind not in allowed:
            raise ERPError("unsupported_command", "Essa operação não está disponível no ERP local.")
        if set(p) - allowed[kind]:
            raise ERPError(
                "unsupported_fields", "A operação contém campos não suportados nesta etapa."
            )
        if kind == "demo.seed":
            if db.execute("SELECT 1 FROM products LIMIT 1").fetchone():
                raise ERPError(
                    "demo_requires_empty", "O cenário inicial exige um banco vazio.", 409
                )
            for identifier, name in [("geral", "Geral"), ("retornos", "Retornos")]:
                db.execute("INSERT INTO deposits VALUES (?,?)", (identifier, name))
            for sku, name in [
                ("demo.aparelho", "Aparelho de demonstração"),
                ("demo.fone", "Fone de demonstração"),
            ]:
                db.execute("INSERT INTO products VALUES (?,?,?,?)", (sku, name, "simple", "[]"))
            components = [
                {"sku": "demo.aparelho", "quantity": "1"},
                {"sku": "demo.fone", "quantity": "1"},
            ]
            db.execute(
                "INSERT INTO products VALUES (?,?,?,?)",
                ("demo.kit", "Kit aparelho + fone", "kit", dumps(components)),
            )
            self.move(db, key, "demo.aparelho", "geral", Decimal(8), Decimal(0), "cenário fictício")
            self.move(db, key, "demo.fone", "geral", Decimal(5), Decimal(0), "cenário fictício")
            return {"seeded": True}
        if kind == "depot.create":
            identifier, name = text(p.get("id"), "código do depósito"), text(p.get("name"), "nome")
            if db.execute("SELECT 1 FROM deposits WHERE id=?", (identifier,)).fetchone():
                raise ERPError("duplicate_depot", "Depósito já cadastrado.", 409)
            db.execute("INSERT INTO deposits VALUES (?,?)", (identifier, name))
            return {"id": identifier}
        if kind == "product.create":
            sku, name = text(p.get("sku"), "SKU"), text(p.get("name"), "nome")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+\-]{0,99}", sku):
                raise ERPError("invalid_sku", "SKU contém caracteres não permitidos.")
            if db.execute("SELECT 1 FROM products WHERE sku=?", (sku,)).fetchone():
                raise ERPError("duplicate_sku", "SKU já cadastrado.", 409)
            product_kind = p.get("kind", "simple")
            if product_kind not in {"simple", "kit", "service"}:
                raise ERPError("invalid_kind", "Escolha produto simples, kit ou serviço.")
            components = (
                self.items(db, p.get("components"), for_components=True)
                if product_kind == "kit"
                else []
            )
            if product_kind != "kit" and p.get("components"):
                raise ERPError("invalid_components", "Produto simples não possui composição.")
            if len({i["sku"] for i in components}) != len(components):
                raise ERPError("duplicate_component", "Agrupe a quantidade de cada componente.")
            components = [
                {"sku": s, "quantity": str(number(q))}
                for s, q in self.requirements(db, components).items()
            ]
            db.execute(
                "INSERT INTO products VALUES (?,?,?,?)",
                (sku, name, product_kind, dumps(components)),
            )
            if product_kind == "kit":
                product_structures.apply(
                    self,
                    db,
                    key,
                    {
                        "sku": sku,
                        "version": 0,
                        "components": [
                            {"sku": i["sku"], "quantity": i["quantity"]} for i in p["components"]
                        ],
                        "reason": "Composição inicial do cadastro próprio",
                    },
                )
            db.execute(
                "INSERT INTO product_pricing VALUES (?,?,?)",
                (
                    sku,
                    str(number(p.get("price", "0"), money=True, unit=True, zero=True)),
                    str(number(p.get("cost_price", "0"), money=True, unit=True, zero=True)),
                ),
            )
            if any(field in p for field in product_details.DETAIL_FIELDS):
                product_details.apply(
                    self,
                    db,
                    key,
                    {
                        "sku": sku,
                        "version": 0,
                        "data": {
                            field: p[field] for field in product_details.DETAIL_FIELDS if field in p
                        },
                    },
                )
            return {"sku": sku}
        if kind in {"stock.post", "stock.transfer"}:
            sku, depot = text(p.get("sku"), "SKU"), self.depot(db, p.get("depot"))
            deposits.require_active(db, depot)
            operation = p.get("type")
            quantity = number(p.get("quantity"), zero=operation == "B" and kind == "stock.post")
            reason = text(p.get("reason"), "motivo", 300)
            if kind == "stock.transfer":
                destination = self.depot(db, p.get("destination"))
                deposits.require_active(db, destination)
                if destination == depot:
                    raise ERPError("same_depot", "Origem e destino devem ser diferentes.")
                cost = procurement.average(self, db, sku, depot)
                self.move(db, key, sku, depot, -quantity, Decimal(0), reason)
                self.move(
                    db,
                    key,
                    sku,
                    destination,
                    quantity,
                    Decimal(0),
                    reason,
                    cost if cost is not None else "unknown",
                )
            else:
                if operation not in {"E", "S", "B"}:
                    raise ERPError("invalid_operation", "Escolha entrada, saída ou balanço.")
                physical, _ = self.balance(db, sku, depot)
                delta = (
                    quantity
                    if operation == "E"
                    else -quantity
                    if operation == "S"
                    else quantity - physical
                )
                cost = (
                    str(number(p["unit_cost"], unit=True, zero=True))
                    if p.get("unit_cost") not in (None, "")
                    else None
                )
                # Bling's "Preço de compra" of the entry: kept with the operation, shown in history.
                if p.get("purchase_price") not in (None, ""):
                    number(p["purchase_price"], unit=True, zero=True)
                self.move(db, key, sku, depot, delta, Decimal(0), reason, cost)
            return {"sku": sku, "depot": depot}
        if kind == "order.import":
            channel, store, external = (
                text(p.get(n), n) for n in ("channel", "store", "external_id")
            )
            external_key = dumps([channel, store, external])
            previous = db.execute(
                "SELECT id,data,status FROM documents WHERE external_key=?", (external_key,)
            ).fetchone()
            if previous and previous["status"] == "draft":
                raise ERPError(
                    "order_draft_review",
                    "Confirme o pedido em digitação antes de processar o evento do canal.",
                    409,
                )
            items = self.items(db, p.get("items"), historical=bool(previous))
            depot = self.depot(db, p.get("depot"))
            if not previous:
                deposits.require_active(db, depot, sale=True)
            metadata = {}
            if p.get("customer_name"):
                metadata["customer_name"] = text(p["customer_name"], "cliente")
            if p.get("contact_id"):
                old_contact = json.loads(previous["data"]).get("contact") if previous else None
                frozen = (
                    old_contact
                    if old_contact and old_contact["id"] == p["contact_id"]
                    else contacts.document_snapshot(db, p["contact_id"])
                )
                if metadata.get("customer_name") and metadata["customer_name"] != frozen["name"]:
                    raise ERPError(
                        "contact_name_conflict",
                        "O cliente informado difere do cadastro selecionado.",
                        409,
                    )
                metadata.update(contact=frozen, customer_name=frozen["name"])
            # Stores discount at the order level: what the buyer paid is the order's value.
            for field in ("discount", "freight"):
                if p.get(field) not in (None, ""):
                    metadata[field] = str(number(p[field], money=True, zero=True))
            order_total = (
                Decimal(total(items))
                - Decimal(metadata.get("discount", "0"))
                + Decimal(metadata.get("freight", "0"))
            )
            if order_total < 0:
                raise ERPError("order_discount", "Desconto maior que o valor do pedido.")
            for field, shape in [
                ("recipient", dict),
                ("channel_fees", dict),
                ("purchase_order", str),
                ("fulfillment_mode", str),
            ]:
                if p.get(field):
                    if not isinstance(p[field], shape):
                        raise ERPError("order_fields", "Dados do canal não reconhecidos.")
                    metadata[field] = p[field]
            for field in ["date", "expected_date"]:
                if p.get(field):
                    try:
                        metadata[field] = date.fromisoformat(p[field]).isoformat()
                    except (ValueError, TypeError):
                        raise ERPError(
                            "order_date", "Informe uma data válida para o pedido."
                        ) from None
            if previous:
                old = json.loads(previous["data"])
                if (
                    old["items"] != items
                    or old["depot"] != depot
                    or any(old.get(k) != v for k, v in metadata.items())
                ):
                    raise ERPError(
                        "order_conflict", "O pedido já existe com outros itens ou depósito.", 409
                    )
                return {"id": previous["id"], "duplicate": True}
            data = {
                **metadata,
                "channel": channel,
                "created_at": self.now(),
                "store": store,
                "external_id": external,
                "depot": depot,
                "items": items,
                "requirements": self.requirements(db, items),
                "subtotal": total(items),
                "total": str(order_total),
                "product_snapshots": {
                    i["sku"]: product_details.document_snapshot(self, db, i["sku"]) for i in items
                },
            }
            identifier = str(uuid4())
            for sku, quantity in data["requirements"].items():
                self.move(db, key, sku, depot, Decimal(0), Decimal(quantity), "reserva " + external)
            db.execute(
                "INSERT INTO documents VALUES (?,?,?,?,?)",
                (identifier, "order", external_key, "reserved", dumps(data)),
            )
            return {"id": identifier, "duplicate": False}
        if kind in {"order.cancel", "order.ship", "order.return"}:
            _, source_order = self.document(db, p.get("id"), "order")
            if source_order.get("pos") and not _pos:
                raise ERPError(
                    "pos_flow", "Use a frente de caixa ou a devolução própria desta venda.", 409
                )
            if (
                kind == "order.return"
                and db.execute(
                    "SELECT 1 FROM return_cases WHERE order_id=? AND status!='cancelled'",
                    (p.get("id"),),
                ).fetchone()
            ):
                raise ERPError(
                    "own_return_case",
                    "Conclua o recebimento pela devolução própria registrada.",
                    409,
                )
            if kind == "order.return" and p.get("condition", "new") != "new":
                raise ERPError(
                    "return_condition", "Nesta etapa, só há devolução integral de produto novo."
                )
            doc, data = self.document(db, p.get("id"), "order")
            if kind == "order.cancel" and doc["status"] == "draft":
                if "situation" in data:
                    data["situation"] = "cancelled"
                data["version"] = data.get("version", 0) + 1
                self.update_document(db, doc["id"], "cancelled", data)
                return {"id": doc["id"], "status": "cancelled"}
            if (
                not _logistics
                and kind != "order.cancel"
                and db.execute(
                    "SELECT 1 FROM logistics_shipments WHERE id=?", (doc["id"],)
                ).fetchone()
            ):
                raise ERPError("logistics_required", "Use o fluxo de logística deste pedido.", 409)
            target = {
                "order.cancel": "cancelled",
                "order.ship": "shipped",
                "order.return": "returned",
            }[kind]
            if doc["status"] == target:
                return {"id": doc["id"], "unchanged": True}
            required = "shipped" if kind == "order.return" else "reserved"
            if doc["status"] != required:
                raise ERPError(
                    "invalid_transition", "Essa ação não é permitida na situação atual.", 409
                )
            depot = (
                self.depot(db, p.get("destination")) if kind == "order.return" else data["depot"]
            )
            for sku, q in data["requirements"].items():
                quantity = Decimal(q)
                physical = (
                    quantity
                    if kind == "order.return"
                    else -quantity
                    if kind == "order.ship"
                    else Decimal(0)
                )
                reserved = Decimal(0) if kind == "order.return" else -quantity
                if kind == "order.ship":
                    cost = procurement.average(self, db, sku, depot)
                    data.setdefault("shipped_costs", {})[sku] = (
                        str(cost) if cost is not None else None
                    )
                cost = (
                    data.get("shipped_costs", {}).get(sku) or "unknown"
                    if kind == "order.return"
                    else None
                )
                self.move(
                    db, key, sku, depot, physical, reserved, kind + " " + data["external_id"], cost
                )
            if kind == "order.ship":
                data["stock_posted"] = True
                if data.get("accounts_policy", "on_dispatch") == "on_dispatch":
                    self.account(db, doc["id"], "receivable", data["total"])
            if kind == "order.return":
                data["stock_posted"] = False
                sales.cancel_receivable(self, db, doc["id"])
                data["return_depot"] = depot
            if kind == "order.cancel":
                sales.cancel_receivable(self, db, doc["id"])
            if kind in {"order.cancel", "order.return"} and "situation" in data:
                data["situation"] = target
            data["version"] = data.get("version", 0) + 1
            self.update_document(db, doc["id"], target, data)
            if kind == "order.cancel":
                logistics.cancel_for_order(db, doc["id"])
            return {"id": doc["id"], "status": target}
        raise ERPError("unsupported_command", "Essa operação não está disponível no ERP local.")

    def snapshot(self):
        with self.connection() as db:
            db.execute("BEGIN")
            from app.erp_local import categories, product_details, product_structures

            products = []
            pricing = {r["sku"]: dict(r) for r in db.execute("SELECT * FROM product_pricing")}
            from app.erp_local import product_images, product_suppliers, product_tax

            suppliers = product_suppliers.snapshot(db)
            tax = product_tax.snapshot(db)
            images = product_images.snapshot(db)
            from app.erp_local.deposits import snapshot as depot_snapshot

            deposits = depot_snapshot(db)
            for row in db.execute("SELECT rowid AS position,* FROM products ORDER BY sku"):
                product = dict(row)
                product["components"] = json.loads(product["components"])
                product["price"] = pricing.get(product["sku"], {}).get("price")
                product["cost_price"] = pricing.get(product["sku"], {}).get("cost_price")
                product["origin"] = self.origin(db, product["sku"])
                product["details"] = product_details.details(db, product["sku"])
                product["suppliers"] = suppliers.get(product["sku"], {"version": 0, "rows": []})
                product["tax"] = tax.get(
                    product["sku"], {"version": 0, "data": product_tax.empty()}
                )
                product["images"] = images.get(product["sku"], {"version": 0, "rows": []})
                product["structure"] = product_structures.details(db, row)
                if product["structure"]["version"]:
                    product["structure"]["history"] = product_structures.history(db, product["sku"])
                product["category"] = categories.document_snapshot(db, product["sku"])
                product["balances"] = []
                for depot in deposits if product["kind"] != "service" else []:
                    if product["kind"] == "kit":
                        values = [
                            (self.balance(db, p["sku"], depot["id"]), Decimal(p["quantity"]))
                            for p in product["components"]
                        ]
                        unknown = not values or any(
                            (self.origin(db, part["sku"]) or {}).get("stock_state", "known")
                            != "known"
                            for part in product["components"]
                        )
                        physical = min(v[0][0] // v[1] for v in values) if not unknown else None
                        available = (
                            min((v[0][0] - v[0][1]) // v[1] for v in values)
                            if not unknown
                            else None
                        )
                        # This is derived capacity, not an independently reserved stock.
                        reserved = None
                    else:
                        physical, reserved = self.balance(db, product["sku"], depot["id"])
                        available = physical - reserved
                        if product["origin"] and product["origin"]["stock_state"] != "known":
                            physical = reserved = available = None
                    product["balances"].append(
                        {
                            "depot": depot["id"],
                            "in_total": not depot["exclude_from_total"],
                            "sellable": depot["status"] == "active"
                            and depot["purpose"] != "quarantine",
                            "physical": str(physical) if physical is not None else None,
                            "reserved": str(reserved) if reserved is not None else None,
                            "available": str(available) if available is not None else None,
                        }
                    )
                products.append(product)
            documents = [
                {**dict(r), "data": json.loads(r["data"])}
                for r in db.execute("SELECT * FROM documents ORDER BY rowid DESC")
            ]
            movements = [
                dict(r) for r in db.execute("SELECT * FROM movements ORDER BY id DESC LIMIT 500")
            ]
            events = [
                dict(r)
                for r in db.execute(
                    "SELECT seq,operation_key,kind,actor,created_at FROM events "
                    "ORDER BY seq DESC LIMIT 100"
                )
            ]
            accounts = [dict(r) for r in db.execute("SELECT * FROM accounts ORDER BY rowid DESC")]
            imported = db.execute("SELECT data FROM catalog_import WHERE id=1").fetchone()
            return {
                "mode": "local",
                "external_connections": False,
                "products": products,
                "deposits": deposits,
                "orders": [d for d in documents if d["kind"] == "order"],
                "purchases": [d for d in documents if d["kind"] == "purchase"],
                "movements": movements,
                "events": events,
                "accounts": accounts,
                "catalog_import": json.loads(imported[0]) if imported else None,
            }
