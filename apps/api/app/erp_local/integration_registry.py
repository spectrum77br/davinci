"""Observed per-account rules. Exact store IDs, no credentials or publisher."""

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from app.erp_local.core import ERPError, dumps, text

COMMANDS = {"channel.policy.override"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS store_policies (
 channel TEXT NOT NULL, store TEXT NOT NULL, status TEXT NOT NULL,
 data TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(channel,store));
CREATE TABLE IF NOT EXISTS integration_bindings_meta (
 id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS integration_authorization_reads (
 integration_id TEXT PRIMARY KEY, platform TEXT NOT NULL, data TEXT NOT NULL);
"""
PROFILES_FILE = Path(__file__).with_name("store_profiles.json")
PROVIDER_TYPES = {
    "25": "ml",
    "316": "shopee",
    "92": "amazon",
    "14849079543": "tiktok",
    "14849083398": "temu",
    "14849079421": "shein",
    "13485688333": "aliexpress",
    "137": "carrefour",
    "169": "magalu",
}


def observed_profiles():
    content = json.loads(PROFILES_FILE.read_text())
    if content.get("version") != 1 or content.get("source") != "observed_bling_forms":
        raise ERPError("store_profiles", "Referência de configurações não reconhecida.")
    return content


def cancellation_rule(profile):
    mappings = [
        m["bling"] for m in profile["status_mapping"] if m["origem"].casefold() == "cancelado"
    ]
    return "cancel" if mappings == ["Cancelado"] else "review"


def build(reference, bindings=()):
    source = observed_profiles()
    profiles = {p["bling_store_id"]: p for p in source["profiles"]}
    integrations = reference.all_rows("integrations")
    stores = reference.all_rows("stores")
    policies, used = [], set()
    for integration in integrations:
        if integration["platform"] == "bling":
            continue
        possible = {
            str(s["bling_store_id"])
            for s in stores
            if s["integration_id"] == integration["id"] and s["bling_store_id"]
        }
        if integration["bling_loja_id"]:
            possible.add(str(integration["bling_loja_id"]))
        possible.update(
            str(b["bling_loja_id"])
            for b in bindings
            if b["integration_id"] == integration["id"]
            and b["plataforma"] == integration["platform"]
        )
        profile = profiles.get(next(iter(possible))) if len(possible) == 1 else None
        if profile and PROVIDER_TYPES.get(profile["provider_type"]) != integration["platform"]:
            profile = None
        active = integration["status"] == "active" and not integration["archived_at"]
        status = "shadow" if profile and active else "inactive" if not active else "identity_review"
        data = {
            "name": integration["name"],
            "platform": integration["platform"],
            "integration_id": integration["id"],
            "active": active,
            "credentials_present": integration["credentials_present"],
            "token_expires_at": integration["token_expires_at"],
            "last_test_at": integration["last_test_at"],
            "last_test_ok": integration["last_test_ok"],
            "credentials_revision": integration.get("updated_at"),
            "candidate_bling_ids": sorted(possible),
            "source_profile": profile,
            "cancellation": cancellation_rule(profile) if profile else "review",
            "automatic_orders_observed": profile["automatic_orders"] if profile else None,
            "source_stock_enabled": profile["source_stock_enabled"] if profile else None,
            "observed_on": source["observed_on"],
            "own_publication": "disabled",
            "direct_authorization_verified": False,
        }
        if profile:
            used.add(profile["bling_store_id"])
        # OAuth renewal / a health check does not change a cancellation rule.
        # Only business/identity configuration changes invalidate the override.
        rule_data = {
            k: v
            for k, v in data.items()
            if k
            not in {
                "credentials_revision",
                "credentials_present",
                "token_expires_at",
                "last_test_at",
                "last_test_ok",
                "direct_authorization_verified",
            }
        }
        data["source_digest"] = hashlib.sha256(dumps(rule_data).encode()).hexdigest()
        policies.append(
            {
                "channel": integration["platform"],
                "store": integration["id"],
                "status": status,
                "data": data,
            }
        )
    assignments = Counter(
        p["data"]["source_profile"]["bling_store_id"] for p in policies if p["status"] == "shadow"
    )
    for p in policies:
        profile = p["data"]["source_profile"]
        if profile and p["status"] == "shadow" and assignments[profile["bling_store_id"]] > 1:
            p["status"] = "identity_review"
            p["data"]["binding_issue"] = "duplicate_active_accounts"
            p["data"]["cancellation"] = "review"
    return policies, [p for p in profiles.values() if p["bling_store_id"] not in used]


def prepare(erp, reference):
    with erp.connection() as db:
        proof = db.execute("SELECT data FROM integration_bindings_meta WHERE id=1").fetchone()
    policies, unbound = build(reference, json.loads(proof[0])["bindings"] if proof else ())
    digest = hashlib.sha256(dumps([2, policies, unbound]).encode()).hexdigest()
    with erp.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute(
            "SELECT 1 FROM events WHERE operation_key=?", ("policies-" + digest,)
        ).fetchone()
        if old:
            return {"replayed": True, "policies": len(policies), "unbound_profiles": len(unbound)}
        db.execute("UPDATE store_policies SET status='inactive'")
        for p in policies:
            previous = policy(db, p["channel"], p["store"])
            if previous and previous.get("override"):
                override = previous["override"]
                p["data"]["override"] = {
                    **override,
                    "status": "active"
                    if override["source_digest"] == p["data"]["source_digest"]
                    else "needs_review",
                }
            db.execute(
                "INSERT INTO store_policies VALUES (?,?,?,?,?) ON CONFLICT(channel,store) "
                "DO UPDATE SET status=excluded.status,data=excluded.data,"
                "updated_at=excluded.updated_at",
                (p["channel"], p["store"], p["status"], dumps(p["data"]), erp.now()),
            )
            reason = (
                "store_policy_review"
                if p["status"] != "shadow"
                else "source_stock_disabled"
                if p["data"]["source_stock_enabled"] is False
                else None
            )
            if reason:
                db.execute(
                    "UPDATE stock_intents SET status='blocked',reason=? "
                    "WHERE status='preview' AND route_id IN "
                    "(SELECT id FROM stock_routes WHERE channel=? AND store=?)",
                    (reason, p["channel"], p["store"]),
                )
        result = {
            "policies": len(policies),
            "bound_active": sum(p["status"] == "shadow" for p in policies),
            "unbound_profiles": len(unbound),
            "external_writes": False,
        }
        db.execute(
            "INSERT INTO events(operation_key,fingerprint,kind,actor,payload,result,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                "policies-" + digest,
                digest,
                "channels.policies.prepare",
                "conferência local",
                "{}",
                dumps(result),
                erp.now(),
            ),
        )
        db.commit()
        return {**result, "replayed": False}


def record_bindings(erp, read):
    """Import only the fixed metadata projection produced by a read-only server query."""
    fields = {"version", "source", "read_only", "captured_at", "bindings"}
    row_fields = {"id", "plataforma", "integration_id", "bling_loja_id", "ativo", "atualizado_em"}
    if (
        set(read) != fields
        or read["version"] != 1
        or read["source"] != "davinci_existing_account_bindings"
        or read["read_only"] != "on"
    ):
        raise ERPError(
            "binding_proof", "A origem deve confirmar a projeção de vínculos somente de leitura."
        )
    try:
        stamp = datetime.fromisoformat(read["captured_at"])
        if stamp.tzinfo is None:
            raise ValueError
        for row in read["bindings"]:
            if set(row) != row_fields or not str(row["bling_loja_id"]).isdigit():
                raise ValueError
            for field in ["id", "integration_id", "plataforma"]:
                text(row[field], field)
    except (ValueError, TypeError, KeyError):
        raise ERPError("binding_proof", "Formato de vínculos não reconhecido.") from None
    encoded = dumps(read)
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    with erp.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute("SELECT data FROM integration_bindings_meta WHERE id=1").fetchone()
        if previous and datetime.fromisoformat(json.loads(previous[0])["captured_at"]) > stamp:
            raise ERPError(
                "binding_proof", "A captura de vínculos é anterior à que já foi conferida.", 409
            )
        if db.execute(
            "SELECT 1 FROM events WHERE operation_key=?", ("binding-" + digest,)
        ).fetchone():
            return {"replayed": True}
        db.execute(
            "INSERT INTO integration_bindings_meta VALUES (1,?) ON CONFLICT(id) "
            "DO UPDATE SET data=excluded.data",
            (encoded,),
        )
        result = {"bindings": len(read["bindings"]), "external_writes": False}
        db.execute(
            "INSERT INTO events(operation_key,fingerprint,kind,actor,payload,result,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                "binding-" + digest,
                digest,
                "channels.bindings.read",
                "conferência local",
                "{}",
                dumps(result),
                erp.now(),
            ),
        )
        db.commit()
        return result


def policy(db, channel, store):
    row = db.execute(
        "SELECT status,data FROM store_policies WHERE channel=? AND store=?", (channel, store)
    ).fetchone()
    return {"status": row[0], **json.loads(row[1])} if row else None


def authorization_stamp(value):
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError
    return stamp


def record_authorizations(erp, read):
    """Persist the secret-free, fixed GET projection; never enables a publisher."""
    from uuid import UUID

    from app.erp_local.authorization_probe import ENDPOINTS, STATES

    try:
        if (
            not isinstance(read, dict)
            or set(read)
            != {
                "version",
                "source",
                "read_only",
                "no_token_refresh",
                "external_business_writes",
                "captured_at",
                "accounts",
            }
            or read["version"] != 1
            or read["source"] != "davinci_native_authorization_read"
            or read["read_only"] != "on"
            or read["no_token_refresh"] is not True
            or read["external_business_writes"] is not False
        ):
            raise ValueError
        captured = authorization_stamp(read["captured_at"])
        if captured.timestamp() > datetime.now(UTC).timestamp() + 300:
            raise ValueError
        if not isinstance(read["accounts"], list) or not 1 <= len(read["accounts"]) <= 64:
            raise ValueError
        identities = set()
        for row in read["accounts"]:
            if set(row) != {
                "integration_id",
                "platform",
                "credentials_revision",
                "checked_at",
                "state",
                "http_status",
                "authorization_valid",
                "identity_match",
            }:
                raise ValueError
            identity = str(UUID(row["integration_id"]))
            checked = authorization_stamp(row["checked_at"])
            revision = authorization_stamp(row["credentials_revision"])
            if (
                identity != row["integration_id"]
                or identity in identities
                or row["platform"] not in ENDPOINTS
                or row["state"] not in STATES
                or checked > captured
                or (captured - checked).total_seconds() > 600
                or revision > captured
                or type(row["authorization_valid"]) is not bool
                or (row["identity_match"] is not None and type(row["identity_match"]) is not bool)
                or (
                    row["http_status"] is not None
                    and (
                        type(row["http_status"]) is not int or not 100 <= row["http_status"] <= 599
                    )
                )
            ):
                raise ValueError
            valid = row["state"] in {
                "read_ok",
                "identity_review",
                "identity_mismatch",
                "shop_inactive",
            }
            if (
                row["authorization_valid"] != valid
                or (valid and row["http_status"] != 200)
                or (row["state"] == "read_ok" and row["identity_match"] is not True)
                or (row["state"] == "identity_review" and row["identity_match"] is not None)
            ):
                raise ValueError
            identities.add(identity)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ERPError(
            "authorization_proof", "A conferência direta tem formato inválido."
        ) from None
    digest = hashlib.sha256(dumps(read).encode()).hexdigest()
    with erp.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        for row in read["accounts"]:
            previous = db.execute(
                "SELECT data FROM integration_authorization_reads WHERE integration_id=?",
                (row["integration_id"],),
            ).fetchone()
            if previous and authorization_stamp(
                json.loads(previous[0])["checked_at"]
            ) > authorization_stamp(row["checked_at"]):
                raise ERPError("authorization_proof", "Já existe conferência mais recente.", 409)
        if db.execute(
            "SELECT 1 FROM events WHERE operation_key=?", ("auth-read-" + digest,)
        ).fetchone():
            return {"replayed": True, "accounts": len(read["accounts"])}
        for row in read["accounts"]:
            db.execute(
                "INSERT INTO integration_authorization_reads VALUES (?,?,?) "
                "ON CONFLICT(integration_id) DO UPDATE SET platform=excluded.platform,"
                "data=excluded.data",
                (row["integration_id"], row["platform"], dumps(row)),
            )
        result = {"accounts": len(read["accounts"]), "external_writes": False}
        db.execute(
            "INSERT INTO events(operation_key,fingerprint,kind,actor,payload,result,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                "auth-read-" + digest,
                digest,
                "channels.authorization.read",
                "conferência local",
                "{}",
                dumps(result),
                erp.now(),
            ),
        )
        db.commit()
    return {**result, "replayed": False}


def authorization_snapshot(db, policy_data):
    row = db.execute(
        "SELECT data FROM integration_authorization_reads WHERE integration_id=? AND platform=?",
        (policy_data["integration_id"], policy_data["platform"]),
    ).fetchone()
    if not row:
        return None
    proof = json.loads(row[0])
    try:
        age = (datetime.now(UTC) - authorization_stamp(proof["checked_at"])).total_seconds()
        same_revision = authorization_stamp(proof["credentials_revision"]) == authorization_stamp(
            policy_data["credentials_revision"]
        )
    except (ValueError, TypeError, KeyError, AttributeError):
        same_revision, age = False, 0
    return {
        **proof,
        "current": same_revision and 0 <= age <= 3600,
        "scope": "identity_or_catalog_read_only",
        "publication_enabled": False,
    }


def cancellation_gate(db, payload):
    rule = policy(db, payload["channel"], payload["store"])
    if not rule or rule["status"] != "shadow":
        return False
    override = rule.get("override", {})
    decision = (
        override.get("cancellation")
        if override.get("status") == "active"
        and override.get("source_digest") == rule["source_digest"]
        else rule["cancellation"]
    )
    return decision == "cancel"


def apply(erp, db, p):
    if set(p) - {"channel", "store", "cancellation", "reason"}:
        raise ERPError("unsupported_fields", "Campos de configuração não reconhecidos.")
    channel, store = text(p.get("channel"), "canal"), text(p.get("store"), "conta")
    row = policy(db, channel, store)
    if not row or row["status"] != "shadow":
        raise ERPError(
            "store_policy_review", "Concilie a identidade desta conta antes de mudar regras.", 409
        )
    decision = p.get("cancellation")
    if decision not in {"review", "cancel"}:
        raise ERPError("store_policy_review", "Escolha conferência ou cancelamento local.")
    reason = text(p.get("reason"), "motivo")
    row["override"] = {
        "status": "active",
        "cancellation": decision,
        "reason": reason,
        "source_digest": row["source_digest"],
        "updated_at": erp.now(),
    }
    row.pop("status")
    db.execute(
        "UPDATE store_policies SET data=?,updated_at=? WHERE channel=? AND store=?",
        (dumps(row), erp.now(), channel, store),
    )
    return {"channel": channel, "store": store, "cancellation": decision, "external_writes": False}


def snapshot(db):
    policies = [
        {**dict(r), "data": json.loads(r["data"])}
        for r in db.execute("SELECT * FROM store_policies ORDER BY channel,store")
    ]
    for p in policies:
        p["data"]["authorization_read"] = authorization_snapshot(db, p["data"])
    used = {
        p["data"]["source_profile"]["bling_store_id"]
        for p in policies
        if p["data"].get("source_profile")
    }
    source = observed_profiles()
    return {
        "policies": policies,
        "observed_on": source["observed_on"],
        "observed_profiles": len(source["profiles"]),
        "unbound_profiles": [p for p in source["profiles"] if p["bling_store_id"] not in used],
        "external_writes": False,
    }
