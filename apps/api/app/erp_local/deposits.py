"""Own depots: total inclusion, default, status and explicit quarantine policy."""

from decimal import Decimal
from uuid import uuid4

from app.erp_local.core import ERPError, text

SCHEMA = """
CREATE TABLE IF NOT EXISTS own_depot_settings (
 id TEXT PRIMARY KEY REFERENCES deposits(id), version INTEGER NOT NULL,
 status TEXT NOT NULL, exclude_from_total INTEGER NOT NULL,
 purpose TEXT NOT NULL, is_default INTEGER NOT NULL, updated_at TEXT NOT NULL);
"""
COMMANDS = {"depot.save"}


def snapshot(db):
    rows = [
        dict(r)
        for r in db.execute(
            "SELECT d.id,d.name,s.version,s.status,s.exclude_from_total,s.purpose,s.is_default "
            "FROM deposits d LEFT JOIN own_depot_settings s ON s.id=d.id "
            "ORDER BY d.name COLLATE NOCASE,d.id"
        )
    ]
    default = next((r["id"] for r in rows if r["is_default"] and r["status"] == "active"), None)
    if not default:
        default = next(
            (r["id"] for r in rows if r["id"] == "geral" and r["status"] != "inactive"), None
        )
    if not default:
        default = next(
            (r["id"] for r in rows if r["status"] != "inactive" and r["purpose"] != "quarantine"),
            None,
        )
    return [
        {
            **r,
            "version": r["version"] or 0,
            "status": r["status"] or "active",
            "purpose": r["purpose"] or "stock",
            "exclude_from_total": bool(r["exclude_from_total"]),
            "is_default": r["id"] == default,
        }
        for r in rows
    ]


def metadata(db, identifier):
    result = next((r for r in snapshot(db) if r["id"] == identifier), None)
    if not result:
        raise ERPError("depot_not_found", "Depósito não encontrado.", 404)
    return result


def require_active(db, identifier, *, sale=False):
    data = metadata(db, identifier)
    if data["status"] != "active":
        raise ERPError("depot_inactive", "Depósito inativo para novas operações.", 409)
    if sale and data["purpose"] == "quarantine":
        raise ERPError(
            "depot_quarantine", "Peças em quarentena não estão liberadas para venda.", 409
        )
    return identifier


def apply(erp, db, key, p):
    from app.erp_local.channels import capture_stock_changes

    if not isinstance(p, dict) or set(p) - {
        "id",
        "version",
        "name",
        "status",
        "exclude_from_total",
        "purpose",
        "is_default",
    }:
        raise ERPError("depot_fields", "Campos do depósito inválidos.")
    previous = metadata(db, p["id"]) if p.get("id") else None
    version = previous["version"] if previous else 0
    if type(p.get("version", 0)) is not int or p.get("version", 0) != version:
        raise ERPError("depot_version", "O depósito mudou. Atualize antes de salvar.", 409)
    name = text(p.get("name"), "descrição")
    status, purpose = p.get("status", "active"), p.get("purpose", "stock")
    excluded, default = p.get("exclude_from_total", False), p.get("is_default", False)
    if (
        not isinstance(status, str)
        or status not in {"active", "inactive"}
        or not isinstance(purpose, str)
        or purpose not in {"stock", "quarantine"}
        or not isinstance(excluded, bool)
        or not isinstance(default, bool)
    ):
        raise ERPError("depot_settings", "Situação e opções do depósito inválidas.")
    if purpose == "quarantine" and not excluded:
        raise ERPError(
            "depot_quarantine", "A quarentena deve ficar fora do saldo geral disponível."
        )
    if default and (status != "active" or purpose != "stock"):
        raise ERPError("depot_default", "O depósito padrão deve estar ativo e liberado para venda.")
    if any(
        r["id"] != (previous or {}).get("id") and r["name"].casefold() == name.casefold()
        for r in snapshot(db)
    ):
        raise ERPError("depot_duplicate", "Já existe um depósito com esta descrição.", 409)
    if previous:
        balances = db.execute(
            "SELECT physical,reserved FROM balances WHERE depot=?", (previous["id"],)
        ).fetchall()
        if status == "inactive" and any(Decimal(r[0]) or Decimal(r[1]) for r in balances):
            raise ERPError(
                "depot_balance",
                "Transfira ou concilie os saldos antes de inativar o depósito.",
                409,
            )
        if (
            status == "inactive"
            and db.execute(
                "SELECT 1 FROM inventory_sessions WHERE depot=? AND status='counting'",
                (previous["id"],),
            ).fetchone()
        ):
            raise ERPError(
                "depot_counting", "Encerre a conferência antes de inativar o depósito.", 409
            )
        if purpose == "quarantine" and any(Decimal(r[1]) for r in balances):
            raise ERPError(
                "depot_reserved",
                "Concilie as reservas antes de colocar o depósito em quarentena.",
                409,
            )
        if (
            previous["is_default"]
            and not default
            and not any(r["is_default"] and r["id"] != previous["id"] for r in snapshot(db))
        ):
            raise ERPError("depot_default", "Defina primeiro outro depósito como padrão.", 409)
        identifier = previous["id"]
        db.execute("UPDATE deposits SET name=? WHERE id=?", (name, identifier))
    else:
        identifier = str(uuid4())
        db.execute("INSERT INTO deposits VALUES (?,?)", (identifier, name))
    if default:
        for current in snapshot(db):
            if current["is_default"] and current["id"] != identifier:
                db.execute(
                    "INSERT INTO own_depot_settings VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) "
                    "DO UPDATE SET version=excluded.version,is_default=0,"
                    "updated_at=excluded.updated_at",
                    (
                        current["id"],
                        current["version"] + 1,
                        current["status"],
                        int(current["exclude_from_total"]),
                        current["purpose"],
                        0,
                        erp.now(),
                    ),
                )
    db.execute(
        "INSERT INTO own_depot_settings VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
        "version=excluded.version,status=excluded.status,exclude_from_total=excluded.exclude_from_total,"
        "purpose=excluded.purpose,is_default=excluded.is_default,updated_at=excluded.updated_at",
        (identifier, version + 1, status, int(excluded), purpose, int(default), erp.now()),
    )
    if previous and (previous["status"] != status or previous["purpose"] != purpose):
        skus = {
            r[0]
            for r in db.execute(
                "SELECT DISTINCT sku FROM stock_routes WHERE depot=? AND active=1", (identifier,)
            )
        }
        capture_stock_changes(erp, db, key, changed_skus=skus)
    return {"id": identifier, "version": version + 1, "external_writes": False}
