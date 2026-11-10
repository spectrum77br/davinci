"""Buyer data kept sealed: one key per sale, wrapped by a master key kept outside the database.

Encryption at rest is AES-256-GCM. A sale's data key lives only in `pii_keys`, never in the event
log, so the log keeps ciphertext only. Erasing a sale's key leaves every copy of its buyer data
unreadable for good (crypto-shredding); that is how the retention window is enforced. Without a
master key, channels whose platform requires encryption (Amazon) are not stored at all, and the
others keep working as before. Backups carry the wrapped keys, never the master key.
"""

import base64
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.erp_local.core import ERPError, dumps

KEY_ENV, KEY_FILE_ENV = "ERP_PII_KEY", "ERP_PII_KEY_FILE"
# Amazon's Data Protection Policy: buyer data encrypted at rest, kept at most 30 days after
# delivery. Shipment comes before delivery, so counting from it is stricter than required.
REQUIRED = {"amazon"}
RETENTION_DAYS = {"amazon": 30}
CLOSED = {"SHIPPED", "DELIVERED", "CANCELED", "CANCELLED", "UNFULFILLABLE"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS pii_keys (
 ref TEXT PRIMARY KEY, channel TEXT NOT NULL, wrapped TEXT, created_at TEXT NOT NULL,
 purged_at TEXT);
"""
PURGED = {"purged": 1}


def master():
    raw = os.environ.get(KEY_ENV) or ""
    path = os.environ.get(KEY_FILE_ENV)
    if not raw and path:
        try:
            raw = Path(path).read_text("utf-8").strip()
        except OSError:
            raw = ""
    if not raw:
        return None
    try:
        key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except (ValueError, TypeError):
        key = b""
    if len(key) != 32:
        raise ERPError("pii_key", "Chave de proteção dos dados inválida no servidor.", 500)
    return key


def _b64(value):
    return base64.urlsafe_b64encode(value).decode()


def _unb64(value):
    return base64.urlsafe_b64decode(value)


def _aes(key):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    return AESGCM(key)


def _data_key(db, ref, channel, key, *, create):
    row = db.execute("SELECT wrapped,purged_at FROM pii_keys WHERE ref=?", (ref,)).fetchone()
    if row and row["purged_at"]:
        return None
    if row:
        blob = _unb64(row["wrapped"])
        return _aes(key).decrypt(blob[:12], blob[12:], ref.encode())
    if not create:
        return None
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    data_key, nonce = AESGCM.generate_key(256), os.urandom(12)
    wrapped = nonce + _aes(key).encrypt(nonce, data_key, ref.encode())
    db.execute(
        "INSERT INTO pii_keys VALUES (?,?,?,?,NULL)",
        (ref, channel, _b64(wrapped), datetime.now(UTC).isoformat()),
    )
    return data_key


def seal(db, ref, channel, value):
    """The value sealed under this sale's key, or as it is when no master key is configured."""
    key = master()
    if key is None:
        if channel in REQUIRED:
            raise ERPError(
                "pii_key_missing",
                "Dados do comprador desta loja só são guardados cifrados; falta a chave no "
                "servidor.",
                409,
            )
        return value
    data_key = _data_key(db, ref, channel, key, create=True)
    if data_key is None:
        raise ERPError("pii_purged", "Os dados deste pedido já foram apagados pelo prazo.", 409)
    nonce = os.urandom(12)
    sealed = nonce + _aes(data_key).encrypt(nonce, dumps(value).encode(), ref.encode())
    return {"sealed": 1, "ref": ref, "data": _b64(sealed)}


def is_sealed(value):
    return isinstance(value, dict) and value.get("sealed") == 1


def open_value(db, value):
    """The original value; None once its key was erased or when the master key is absent."""
    if not is_sealed(value):
        return None if value == PURGED else value
    key = master()
    if key is None:
        return None
    data_key = _data_key(db, value["ref"], "", key, create=False)
    if data_key is None:
        return None
    blob = _unb64(value["data"])
    return json.loads(_aes(data_key).decrypt(blob[:12], blob[12:], value["ref"].encode()))


def _closed_since(db, channel, store, external):
    since = None
    for row in db.execute(
        "SELECT updated_at,data FROM native_order_observations "
        "WHERE channel=? AND store=? AND external_id=? ORDER BY updated_at",
        (channel, store, external),
    ):
        status = str(json.loads(row["data"]).get("status") or "").upper()
        if status in CLOSED:
            since = since or row["updated_at"]
        else:
            since = None
    return since


def purge(erp, now=None):
    """Erase buyer data past each platform's retention window. Fiscal records are not touched."""
    now = now or datetime.now(UTC)
    report = {"purged": 0}
    with erp.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        for channel, days in RETENTION_DAYS.items():
            limit = (now - timedelta(days=days)).isoformat()
            hard = (now - timedelta(days=days + 30)).isoformat()
            rows = db.execute(
                "SELECT store,external_id,data,captured_at FROM native_order_recipients "
                "WHERE channel=?",
                (channel,),
            ).fetchall()
            for row in rows:
                if json.loads(row["data"]) == PURGED:
                    continue
                since = _closed_since(db, channel, row["store"], row["external_id"])
                if not ((since and since <= limit) or row["captured_at"] <= hard):
                    continue
                ref = f"{channel}:{row['store']}:{row['external_id']}"
                db.execute(
                    "UPDATE pii_keys SET wrapped=NULL,purged_at=? WHERE ref=?",
                    (now.isoformat(), ref),
                )
                db.execute(
                    "UPDATE native_order_recipients SET data=? "
                    "WHERE channel=? AND store=? AND external_id=?",
                    (dumps(PURGED), channel, row["store"], row["external_id"]),
                )
                # Readings kept before sealing existed hold the data in the log itself.
                for event in db.execute(
                    "SELECT operation_key,payload FROM events WHERE kind='recipient.capture' "
                    "AND operation_key LIKE ?",
                    (f"recipient-v1:{channel}:{row['store']}:{row['external_id']}:%",),
                ).fetchall():
                    payload = json.loads(event["payload"])
                    projection = payload.get("projection") or {}
                    if is_sealed(projection.get("sealed")):
                        continue
                    projection = {
                        **projection,
                        "buyer": PURGED,
                        "recipient": PURGED,
                    }
                    db.execute(
                        "UPDATE events SET payload=? WHERE operation_key=?",
                        (dumps({**payload, "projection": projection}), event["operation_key"]),
                    )
                report["purged"] += 1
        db.commit()
    return report


def status(db):
    """For the readiness screen: whether sealing is on and how many keys exist or were erased."""
    try:
        row = db.execute(
            "SELECT count(*) AS keys, sum(purged_at IS NOT NULL) AS purged FROM pii_keys"
        ).fetchone()
    except Exception:  # noqa: BLE001 - the table appears with the schema
        row = None
    return {
        "sealing": master() is not None,
        "keys": (row["keys"] if row else 0) or 0,
        "purged": (row["purged"] if row else 0) or 0,
    }
