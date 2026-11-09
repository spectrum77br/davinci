"""Read-only transition reference. It never overwrites the ERP's working balances."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path

from app.erp_local.core import ERPError, LocalERP, dumps

FIELDS = {
    "products": {
        "id",
        "bling_product_id",
        "sku",
        "name",
        "formato",
        "situacao",
        "stock",
        "reserved_stock",
        "saldo_fisico",
        "saldo_virtual_total",
        "price",
        "cost_price",
        "bling_cost_price",
        "updated_at",
    },
    "components": {
        "id",
        "kit_bling_product_id",
        "component_bling_product_id",
        "quantity",
        "synced_at",
    },
    "orders": {
        "id",
        "number",
        "store_number",
        "date",
        "store",
        "situation",
        "total",
        "updated_at",
        "conflicting_situations",
        "items",
    },
    "links": {
        "id",
        "product_id",
        "integration_id",
        "platform",
        "external_id",
        "variation_id",
        "external_sku",
        "listing_title",
        "stock",
        "price",
        "last_sync_status",
        "last_sync_at",
        "updated_at",
    },
    "integrations": {
        "id",
        "platform",
        "name",
        "status",
        "token_expires_at",
        "last_test_at",
        "last_test_ok",
        "archived_at",
        "vacation_mode",
        "bling_loja_id",
        "credentials_present",
        "updated_at",
    },
    "stores": {
        "id",
        "marketplace",
        "status",
        "integration_id",
        "bling_store_id",
        "name",
        "updated_at",
    },
    # Tabela de Preços → Prioridade: the stock lot DaVinci's robot moves each product to.
    "priorities": {"id", "sku", "priority"},
}
ITEM_FIELDS = {"sku", "product_id", "name", "quantity", "unit_price"}
ROOT_FIELDS = {"version", "source", "read_only", "captured_at", "coverage", *FIELDS}
COVERAGE_FIELDS = {
    "products_total",
    "orders_total",
    "orders_eligible",
    "orders_window_days",
    "orders_limit",
    "links_total",
    "links_limit",
}


def validate_reference(data):
    if not isinstance(data, dict) or set(data) != ROOT_FIELDS:
        raise ERPError("invalid_reference", "Formato da referência não reconhecido.")
    if data["version"] != 1 or data["source"] != "davinci_mirror" or data["read_only"] != "on":
        raise ERPError("invalid_reference", "A origem precisa confirmar leitura sem gravação.")
    try:
        stamp = datetime.fromisoformat(data["captured_at"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ERPError("invalid_reference", "Data de captura inválida.") from None
    coverage = data["coverage"]
    if not isinstance(coverage, dict) or set(coverage) != COVERAGE_FIELDS:
        raise ERPError("invalid_reference", "Abrangência da captura inválida.")
    if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in coverage.values()):
        raise ERPError("invalid_reference", "Contagem da captura inválida.")
    for kind, fields in FIELDS.items():
        rows = data[kind]
        if not isinstance(rows, list) or len(rows) > 100000:
            raise ERPError("invalid_reference", "Volume de referência não suportado.")
        ids = set()
        for row in rows:
            if (
                not isinstance(row, dict)
                or set(row) != fields
                or not isinstance(row.get("id"), str)
            ):
                raise ERPError(
                    "invalid_reference", "A captura contém campos fora da projeção permitida."
                )
            if row["id"] in ids:
                raise ERPError("invalid_reference", "Identidade duplicada na captura.")
            ids.add(row["id"])
            if kind == "orders":
                if not isinstance(row["items"], list) or any(
                    not isinstance(i, dict) or set(i) != ITEM_FIELDS for i in row["items"]
                ):
                    raise ERPError("invalid_reference", "Itens fora da projeção permitida.")
    if len(data["products"]) != coverage["products_total"]:
        raise ERPError("invalid_reference", "A cópia do catálogo está incompleta.")
    if len(data["orders"]) != min(coverage["orders_eligible"], coverage["orders_limit"]):
        raise ERPError("invalid_reference", "A cópia dos pedidos está incompleta.")
    if len(data["links"]) != min(coverage["links_total"], coverage["links_limit"]):
        raise ERPError("invalid_reference", "A cópia dos vínculos está incompleta.")
    return data


class SSHReference:
    def __init__(self, host: str):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", host):
            raise ERPError("invalid_source", "Use um alias SSH configurado.")
        self.host = host

    def read(self):
        # Fixed command and curated SQL; neither HTTP callers nor row values become code.
        remote = (
            "docker exec -i -e PGOPTIONS='-c default_transaction_read_only=on "
            "-c statement_timeout=30000 -c lock_timeout=2000' pg18 "
            "psql -X -qAt -U pguser -d davinci -v ON_ERROR_STOP=1"
        )
        try:
            result = subprocess.run(  # noqa: S603 - fixed command and validated SSH alias; no shell.
                [
                    "/usr/bin/ssh",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=10",
                    self.host,
                    remote,
                ],
                input=Path(__file__).with_name("reference.sql").read_text(),
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError):
            raise ERPError(
                "source_unavailable", "A origem não respondeu; a última cópia foi preservada.", 502
            ) from None
        if result.returncode or len(result.stdout) > 64 * 1024 * 1024:
            raise ERPError(
                "source_unavailable",
                "Não foi possível ler a origem; a última cópia foi preservada.",
                502,
            )
        try:
            return validate_reference(json.loads(result.stdout))
        except (json.JSONDecodeError, TypeError):
            raise ERPError(
                "invalid_reference", "A origem retornou uma captura inválida.", 502
            ) from None


def reference_query():
    """The curated SELECT alone, without psql's transaction lines."""
    lines = Path(__file__).with_name("reference.sql").read_text().splitlines()
    keep = [
        line
        for line in lines
        if not line.strip().upper().startswith(("BEGIN", "COMMIT", "ROLLBACK", "SET LOCAL"))
    ]
    return "\n".join(keep).strip().rstrip(";")


class LocalReference:
    """On the server: the same curated query through DaVinci's database settings, read-only."""

    def read(self):
        import asyncio

        async def run():
            from app.db import SessionLocal, engine

            try:
                async with SessionLocal() as session:
                    connection = await session.connection()
                    await connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                    await connection.exec_driver_sql("SET LOCAL statement_timeout = '25s'")
                    value = (await connection.exec_driver_sql(reference_query())).scalar_one()
                    await session.rollback()
                    return value
            finally:
                await engine.dispose()

        try:
            value = asyncio.run(run())
        except Exception:
            raise ERPError(
                "source_unavailable",
                "Não foi possível ler a origem; a última cópia foi preservada.",
                502,
            ) from None
        try:
            return validate_reference(json.loads(value) if isinstance(value, str) else value)
        except (json.JSONDecodeError, TypeError):
            raise ERPError(
                "invalid_reference", "A origem retornou uma captura inválida.", 502
            ) from None


class ReferenceStore:
    def __init__(self, erp: LocalERP, source=None):
        self.erp, self.source = erp, source
        self.lock = threading.Lock()
        with erp.connection() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS reference_meta (
              id INTEGER PRIMARY KEY CHECK(id=1), data TEXT);
            CREATE TABLE IF NOT EXISTS reference_rows (
              kind TEXT, id TEXT, data TEXT NOT NULL, search TEXT NOT NULL, PRIMARY KEY(kind,id));
            CREATE TABLE IF NOT EXISTS reference_attempts (
              id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, ok INTEGER, code TEXT);
            """)

    def replace(self, data):
        data = validate_reference(data)
        digest = hashlib.sha256(dumps(data).encode()).hexdigest()
        metadata = {k: data[k] for k in ["version", "source", "captured_at", "coverage"]}
        metadata.update(
            {
                "digest": digest,
                "counts": {k: len(data[k]) for k in FIELDS},
                "loaded_at": datetime.now(UTC).isoformat(),
                "active_products": sum(p["situacao"] == "A" for p in data["products"]),
                "active_kits": sum(
                    p["situacao"] == "A" and p["formato"] == "E" for p in data["products"]
                ),
            }
        )
        with self.erp.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("DELETE FROM reference_rows")
                for kind in FIELDS:
                    db.executemany(
                        "INSERT INTO reference_rows VALUES (?,?,?,?)",
                        [(kind, r["id"], dumps(r), dumps(r).lower()) for r in data[kind]],
                    )
                db.execute(
                    "INSERT INTO reference_meta VALUES (1,?) "
                    "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                    (dumps(metadata),),
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
        return metadata

    def info(self):
        with self.erp.connection() as db:
            row = db.execute("SELECT data FROM reference_meta WHERE id=1").fetchone()
            last = db.execute(
                "SELECT at,ok,code FROM reference_attempts ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return {
            "configured": self.source is not None,
            "external_writes": False,
            "snapshot": json.loads(row[0]) if row else None,
            "last_attempt": dict(last) if last else None,
            "refreshing": self.lock.locked(),
        }

    def refresh(self):
        if self.source is None:
            raise ERPError(
                "source_not_configured", "A referência de leitura não foi configurada.", 409
            )
        if not self.lock.acquire(blocking=False):
            raise ERPError("refresh_running", "Já existe uma atualização em andamento.", 409)
        code, ok = None, False
        try:
            result = self.replace(self.source.read())
            ok = True
            return result
        except ERPError as e:
            code = e.code
            raise
        finally:
            try:
                with self.erp.connection() as db:
                    db.execute(
                        "INSERT INTO reference_attempts(at,ok,code) VALUES (?,?,?)",
                        (datetime.now(UTC).isoformat(), int(ok), code),
                    )
            finally:
                self.lock.release()

    def rows(self, kind, query="", offset=0, limit=50):
        if kind not in FIELDS:
            raise ERPError("unknown_reference", "Área de referência desconhecida.", 404)
        query = query[:120].lower().replace("^", "^^").replace("%", "^%").replace("_", "^_")
        with self.erp.connection() as db:
            args = (kind, "%" + query + "%")
            count = db.execute(
                "SELECT count(*) FROM reference_rows WHERE kind=? AND search LIKE ? ESCAPE '^'",
                args,
            ).fetchone()[0]
            rows = db.execute(
                "SELECT data FROM reference_rows WHERE kind=? AND search LIKE ? ESCAPE '^' "
                "ORDER BY id LIMIT ? OFFSET ?",
                (*args, limit, offset),
            )
            return {
                "items": [json.loads(r[0]) for r in rows],
                "total": count,
                "offset": offset,
                "limit": limit,
            }

    def all_rows(self, kind):
        if kind not in FIELDS:
            raise ERPError("unknown_reference", "Área de referência desconhecida.", 404)
        with self.erp.connection() as db:
            return [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT data FROM reference_rows WHERE kind=? ORDER BY id", (kind,)
                )
            ]
