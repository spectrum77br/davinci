"""Own warehouse workflow. No carrier client, credentials or outbound transport.

All operations share the core transaction, including dispatch/receivable and
return/stock effects. Tracking is an observation, never proof of physical return.
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from app.erp_local.core import ERPError, dumps, number, text

SCHEMA = """
CREATE TABLE IF NOT EXISTS logistics_shipments (
 id TEXT PRIMARY KEY REFERENCES documents(id), stage TEXT NOT NULL,
 data TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS logistics_scans (
 seq INTEGER PRIMARY KEY AUTOINCREMENT,
 shipment_id TEXT REFERENCES logistics_shipments(id), scan_id TEXT NOT NULL,
 fingerprint TEXT NOT NULL, cycle INTEGER NOT NULL, sku TEXT NOT NULL,
 quantity TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(shipment_id,scan_id));
CREATE TABLE IF NOT EXISTS logistics_manifests (
 id TEXT PRIMARY KEY, status TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS logistics_tracking (
 seq INTEGER PRIMARY KEY AUTOINCREMENT,
 shipment_id TEXT REFERENCES logistics_shipments(id), event_id TEXT NOT NULL,
 fingerprint TEXT NOT NULL, status TEXT NOT NULL, occurred_at TEXT NOT NULL,
 disposition TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(shipment_id,event_id));
CREATE TABLE IF NOT EXISTS logistics_labels (
 id TEXT PRIMARY KEY, shipment_id TEXT REFERENCES logistics_shipments(id),
 format TEXT NOT NULL, sha256 TEXT NOT NULL, content BLOB NOT NULL,
 provenance TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(shipment_id,sha256));
"""

FIELDS = {
    "logistics.prepare": {"id", "service"},
    "logistics.scan": {"id", "scan_id", "sku", "quantity"},
    "logistics.reset": {"id"},
    "logistics.pack": {"id", "packages"},
    "logistics.unpack": {"id"},
    "logistics.label.prepare": {"id"},
    "logistics.label.record": {"id", "label_id"},
    "logistics.dispatch": {"id", "proof"},
    "logistics.manifest.create": {"ids"},
    "logistics.manifest.dispatch": {"id", "proof"},
    "logistics.manifest.cancel": {"id"},
    "logistics.track": {"id", "event_id", "status", "occurred_at"},
    "logistics.return.open": {"id", "reason"},
    "logistics.return.receive": {"id", "receipt_id", "destination", "condition"},
    "orders.batch": {"ids", "action", "service", "status"},
}
COMMANDS = set(FIELDS)
TRACKING_NEXT = {
    None: {
        "in_transit",
        "delivered",
        "delivery_failed",
        "return_in_transit",
        "returned_to_seller",
        "returned_to_carrier",
    },
    "in_transit": {"in_transit", "delivered", "delivery_failed", "return_in_transit"},
    "delivery_failed": {"in_transit", "delivered", "delivery_failed", "return_in_transit"},
    "delivered": {"delivered", "return_in_transit"},
    "return_in_transit": {"return_in_transit", "returned_to_carrier", "returned_to_seller"},
    "returned_to_carrier": {"returned_to_carrier", "return_in_transit", "returned_to_seller"},
    "returned_to_seller": {"returned_to_seller"},
}


def shipment(db, identifier):
    row = db.execute(
        "SELECT * FROM logistics_shipments WHERE id=?", (text(identifier, "pedido"),)
    ).fetchone()
    if not row:
        raise ERPError("shipment_not_found", "Prepare a logística do pedido primeiro.", 404)
    return dict(row), json.loads(row["data"])


def save(db, identifier, stage, data):
    db.execute(
        "UPDATE logistics_shipments SET stage=?,data=? WHERE id=?", (stage, dumps(data), identifier)
    )


def require_stage(row, *stages):
    if row["stage"] not in stages:
        raise ERPError("shipping_transition", "Ação indisponível nesta etapa da logística.", 409)


def ids(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 100:
        raise ERPError("invalid_batch", "Selecione de 1 a 100 pedidos.")
    result = [text(i, "pedido") for i in value]
    if len(set(result)) != len(result):
        raise ERPError("invalid_batch", "Há pedidos repetidos na seleção.")
    return result


def scanned(db, identifier, cycle):
    result = {}
    for row in db.execute(
        "SELECT sku,quantity FROM logistics_scans WHERE shipment_id=? AND cycle=?",
        (identifier, cycle),
    ):
        result[row["sku"]] = result.get(row["sku"], Decimal(0)) + Decimal(row["quantity"])
    return result


def cancel_for_order(db, identifier):
    row = db.execute("SELECT * FROM logistics_shipments WHERE id=?", (identifier,)).fetchone()
    if row:
        data = json.loads(row["data"])
        data["label_request"] = {"status": "cancelled", "external_writes": False}
        save(db, identifier, "cancelled", data)


def dispatch(erp, db, key, identifier, proof, *, manifest=None):
    row, data = shipment(db, identifier)
    require_stage(row, "packed")
    if data.get("manifest_id") != manifest:
        raise ERPError(
            "manifest_required", "Despache pela remessa à qual o pedido está vinculado.", 409
        )
    proof = text(proof, "comprovante local do despacho")
    erp.apply(db, key, "order.ship", {"id": identifier}, _logistics=True)
    data.update(dispatched_at=erp.now(), dispatch_proof=proof)
    save(db, identifier, "dispatched", data)
    return {"id": identifier, "stage": "dispatched", "external_writes": False}


def packages(value, requirements):
    if not isinstance(value, list) or not 1 <= len(value) <= 100:
        raise ERPError("invalid_packages", "Informe de 1 a 100 volumes.")
    result, assigned, codes = [], {}, set()
    for package in value:
        allowed = {"id", "weight_kg", "height_cm", "width_cm", "length_cm", "items"}
        if not isinstance(package, dict) or set(package) != allowed:
            raise ERPError(
                "invalid_packages", "Informe código, peso, dimensões e itens de cada volume."
            )
        code = text(package["id"], "código do volume", 60)
        if code in codes:
            raise ERPError("invalid_packages", "Há códigos de volume repetidos.")
        codes.add(code)
        item = {"id": code}
        for field in ["weight_kg", "height_cm", "width_cm", "length_cm"]:
            n = number(package[field])
            if n > (1000 if field == "weight_kg" else 10000):
                raise ERPError("invalid_packages", "Peso ou dimensão acima do limite local.")
            item[field] = str(n)
        lines = package["items"]
        if not isinstance(lines, list) or not 1 <= len(lines) <= 500:
            raise ERPError("invalid_packages", "Cada volume precisa ter itens.")
        item["items"], seen = [], set()
        for line in lines:
            if not isinstance(line, dict) or set(line) != {"sku", "quantity"}:
                raise ERPError("invalid_packages", "Itens do volume devem conter SKU e quantidade.")
            sku = text(line["sku"], "SKU")
            if sku not in requirements or sku in seen:
                raise ERPError("invalid_packages", "SKU ausente do pedido ou repetido no volume.")
            seen.add(sku)
            q = number(line["quantity"])
            assigned[sku] = assigned.get(sku, Decimal(0)) + q
            item["items"].append({"sku": sku, "quantity": str(q)})
        result.append(item)
    if assigned != {s: Decimal(q) for s, q in requirements.items()}:
        raise ERPError(
            "package_totals", "Os volumes devem conter exatamente todas as peças do pedido."
        )
    return result


def apply(erp, db, key, kind, p):
    if not isinstance(p, dict) or set(p) - FIELDS[kind]:
        raise ERPError("unsupported_fields", "Campos não suportados nesta operação logística.")
    if kind == "orders.batch":
        action = p.get("action")
        if action not in {"prepare", "cancel", "situation"}:
            raise ERPError("invalid_batch", "Escolha preparar logística ou cancelar pedidos.")
        result = []
        for identifier in ids(p.get("ids")):
            doc, _ = erp.document(db, identifier, "order")
            if action == "situation":
                result.append(
                    erp.apply(
                        db, key, "order.situation", {"id": identifier, "status": p.get("status")}
                    )
                )
                continue
            if doc["status"] not in (
                {"reserved"} if action == "prepare" else {"draft", "reserved"}
            ):
                raise ERPError("invalid_batch", "Todos os pedidos precisam estar reservados.", 409)
            if action == "prepare":
                result.append(
                    apply(
                        erp,
                        db,
                        key,
                        "logistics.prepare",
                        {"id": identifier, "service": p.get("service")},
                    )
                )
            else:
                result.append(erp.apply(db, key, "order.cancel", {"id": identifier}))
        return {"results": result, "external_writes": False}
    if kind == "logistics.prepare":
        doc, order = erp.document(db, p.get("id"), "order")
        if doc["status"] != "reserved":
            raise ERPError(
                "shipping_transition", "Somente pedidos reservados entram na separação.", 409
            )
        service = text(p.get("service"), "serviço de entrega")
        previous = db.execute(
            "SELECT data FROM logistics_shipments WHERE id=?", (doc["id"],)
        ).fetchone()
        if previous:
            if json.loads(previous[0])["service"] != service:
                raise ERPError(
                    "shipping_conflict", "O pedido já está preparado com outro serviço.", 409
                )
            return {"id": doc["id"], "unchanged": True}
        data = {
            "requirements": order["requirements"],
            "cycle": 1,
            "packages": [],
            "channel": order["channel"],
            "store": order["store"],
            "depot": order["depot"],
            "external_id": order["external_id"],
            "service": service,
            "mode": "local_only",
        }
        db.execute(
            "INSERT INTO logistics_shipments VALUES (?,?,?,?)",
            (doc["id"], "picking", dumps(data), erp.now()),
        )
        return {"id": doc["id"], "stage": "picking"}
    if kind == "logistics.manifest.create":
        selected = ids(p.get("ids"))
        group, binding = [], None
        identifier = str(uuid4())
        for order_id in selected:
            row, data = shipment(db, order_id)
            require_stage(row, "packed")
            current = [data[k] for k in ["channel", "store", "depot", "service"]]
            if data.get("manifest_id") or (binding and current != binding):
                raise ERPError(
                    "manifest_conflict",
                    "Agrupe apenas pedidos livres da mesma conta, depósito e serviço.",
                    409,
                )
            binding = current
            data["manifest_id"] = identifier
            save(db, order_id, "packed", data)
            group.append(order_id)
        db.execute(
            "INSERT INTO logistics_manifests VALUES (?,?,?,?)",
            (
                identifier,
                "prepared",
                dumps({"ids": group, "binding": binding, "mode": "local_only"}),
                erp.now(),
            ),
        )
        return {"id": identifier, "status": "prepared", "external_writes": False}
    if kind.startswith("logistics.manifest."):
        row = db.execute(
            "SELECT * FROM logistics_manifests WHERE id=?", (text(p.get("id"), "remessa"),)
        ).fetchone()
        if not row or row["status"] != "prepared":
            raise ERPError("manifest_transition", "A remessa precisa estar preparada.", 409)
        data = json.loads(row["data"])
        if kind == "logistics.manifest.dispatch":
            for identifier in data["ids"]:
                dispatch(erp, db, key, identifier, p.get("proof"), manifest=row["id"])
            data.update(dispatched_at=erp.now(), proof=p["proof"])
            status = "dispatched"
        else:
            for identifier in data["ids"]:
                s, d = shipment(db, identifier)
                d.pop("manifest_id", None)
                save(db, identifier, s["stage"], d)
            status = "cancelled"
        db.execute(
            "UPDATE logistics_manifests SET status=?,data=? WHERE id=?",
            (status, dumps(data), row["id"]),
        )
        return {"id": row["id"], "status": status, "external_writes": False}

    row, data = shipment(db, p.get("id"))
    identifier = row["id"]
    if kind == "logistics.scan":
        scan_id = text(p.get("scan_id"), "identificador da leitura")
        sku, quantity = text(p.get("sku"), "SKU"), number(p.get("quantity"))
        fingerprint = hashlib.sha256(dumps([sku, str(quantity)]).encode()).hexdigest()
        old = db.execute(
            "SELECT fingerprint FROM logistics_scans WHERE shipment_id=? AND scan_id=?",
            (identifier, scan_id),
        ).fetchone()
        if old:
            if old[0] != fingerprint:
                raise ERPError(
                    "scan_conflict", "Essa leitura já foi registrada com outros dados.", 409
                )
            return {"id": identifier, "duplicate": True}
        require_stage(row, "picking")
        counts = scanned(db, identifier, data["cycle"])
        if sku not in data["requirements"] or counts.get(sku, Decimal(0)) + quantity > Decimal(
            data["requirements"][sku]
        ):
            raise ERPError(
                "scan_quantity", "SKU ausente ou quantidade maior que a exigida pelo pedido.", 409
            )
        db.execute(
            "INSERT INTO logistics_scans(shipment_id,scan_id,fingerprint,cycle,sku,quantity,"
            "created_at) VALUES (?,?,?,?,?,?,?)",
            (identifier, scan_id, fingerprint, data["cycle"], sku, str(quantity), erp.now()),
        )
        return {"id": identifier, "duplicate": False}
    if kind == "logistics.reset":
        require_stage(row, "picking")
        data["cycle"] += 1
    elif kind == "logistics.pack":
        require_stage(row, "picking")
        if scanned(db, identifier, data["cycle"]) != {
            s: Decimal(q) for s, q in data["requirements"].items()
        }:
            raise ERPError(
                "incomplete_picking", "Confira todas as peças antes de fechar os volumes.", 409
            )
        data["packages"] = packages(p.get("packages"), data["requirements"])
        data["packed_at"] = erp.now()
        row["stage"] = "packed"
    elif kind == "logistics.unpack":
        require_stage(row, "packed")
        if data.get("manifest_id"):
            raise ERPError(
                "manifest_required", "Desfaça a remessa antes de reabrir os volumes.", 409
            )
        data["packages"] = []
        data.pop("label_request", None)
        row["stage"] = "picking"
    elif kind == "logistics.label.prepare":
        require_stage(row, "packed")
        data["label_request"] = {
            "status": "blocked",
            "reason": "Conector direto, autorização da conta e dados de envio "
            "ainda precisam de homologação.",
            "external_writes": False,
        }
    elif kind == "logistics.label.record":
        if not db.execute(
            "SELECT 1 FROM logistics_labels WHERE id=? AND shipment_id=?",
            (p.get("label_id"), identifier),
        ).fetchone():
            raise ERPError("label_not_found", "Documento local não encontrado.", 404)
        return {"id": identifier, "label_id": p["label_id"], "verified": False}
    elif kind == "logistics.dispatch":
        return dispatch(erp, db, key, identifier, p.get("proof"))
    elif kind == "logistics.track":
        return track(erp, db, row, data, p)
    elif kind == "logistics.return.open":
        require_stage(row, "dispatched")
        if data.get("return"):
            raise ERPError("return_exists", "Já há uma devolução aberta para este pedido.", 409)
        data["return"] = {
            "reason": text(p.get("reason"), "motivo"),
            "status": "awaiting_receipt",
            "opened_at": erp.now(),
        }
    elif kind == "logistics.return.receive":
        require_stage(row, "dispatched")
        case = data.get("return")
        if not case or case["status"] != "awaiting_receipt":
            raise ERPError(
                "return_transition",
                "Abra uma devolução antes de receber fisicamente os produtos.",
                409,
            )
        condition = p.get("condition")
        if condition not in {"new", "damaged"}:
            raise ERPError("return_condition", "Informe novo ou avariado.")
        receipt_id = text(p.get("receipt_id"), "comprovante de recebimento")
        destination = erp.depot(db, p.get("destination"))
        if condition == "new":
            erp.apply(
                db,
                key,
                "order.return",
                {"id": identifier, "destination": destination, "condition": "new"},
                _logistics=True,
            )
            row["stage"] = "returned"
        case.update(
            status="received" if condition == "new" else "quarantine_pending",
            condition=condition,
            receipt_id=receipt_id,
            destination=destination,
            received_at=erp.now(),
        )
        # Damaged returns remain outside sellable stock and finance, for inspection.
        # No invented stock lot, supplier credit or refund is posted here.
    save(db, identifier, row["stage"], data)
    return {"id": identifier, "stage": row["stage"], "external_writes": False}


def track(erp, db, row, data, p):
    require_stage(row, "dispatched", "returned")
    event_id, status = text(p.get("event_id"), "evento"), p.get("status")
    if not isinstance(status, str) or status not in TRACKING_NEXT:
        raise ERPError("tracking_status", "Situação de rastreio desconhecida.")
    try:
        stamp = datetime.fromisoformat(p.get("occurred_at", "").replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp > datetime.now(UTC) + timedelta(minutes=5):
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ERPError("tracking_date", "Informe data com fuso, sem horário futuro.") from None
    occurred_at = stamp.astimezone(UTC).isoformat()
    fingerprint = hashlib.sha256(dumps([status, occurred_at]).encode()).hexdigest()
    old = db.execute(
        "SELECT fingerprint,disposition FROM logistics_tracking WHERE shipment_id=? AND event_id=?",
        (row["id"], event_id),
    ).fetchone()
    if old:
        if old[0] != fingerprint:
            raise ERPError(
                "tracking_conflict", "O evento de rastreio já existe com outros dados.", 409
            )
        return {"id": row["id"], "duplicate": True, "disposition": old[1]}
    current = data.get("tracking")
    if stamp < datetime.fromisoformat(data["dispatched_at"]):
        disposition = "before_dispatch"
    elif current and occurred_at <= current["occurred_at"]:
        disposition = "older_or_simultaneous"
    elif status not in TRACKING_NEXT[current["status"] if current else None]:
        disposition = "needs_review"
    else:
        disposition = "applied"
        data["tracking"] = {
            "status": status,
            "occurred_at": occurred_at,
            "source": "operator_local",
        }
        save(db, row["id"], row["stage"], data)
    db.execute(
        "INSERT INTO logistics_tracking(shipment_id,event_id,fingerprint,status,occurred_at,"
        "disposition,created_at) VALUES (?,?,?,?,?,?,?)",
        (row["id"], event_id, fingerprint, status, occurred_at, disposition, erp.now()),
    )
    return {"id": row["id"], "disposition": disposition, "duplicate": False}


def import_label(erp, identifier, format_, raw):
    """Preserve operator-uploaded originals; never treat them as carrier proof."""
    if format_ not in {"pdf", "zpl"} or not 0 < len(raw) <= 2 * 1024 * 1024:
        raise ERPError("label_format", "Selecione PDF ou ZPL de até 2 MiB.")
    if format_ == "pdf":
        import fitz

        try:
            with fitz.open(stream=raw, filetype="pdf") as pdf:
                if pdf.is_encrypted or not 1 <= len(pdf) <= 100:
                    raise ValueError
        except Exception:
            raise ERPError(
                "label_format", "PDF inválido, protegido ou acima de 100 páginas."
            ) from None
    else:
        try:
            zpl = raw.decode("utf-8-sig").strip()
            if not zpl.startswith("^XA") or not zpl.endswith("^XZ"):
                raise ValueError
        except (ValueError, UnicodeError):
            raise ERPError(
                "label_format", "O ZPL deve conter um documento ^XA … ^XZ em UTF-8."
            ) from None
    digest = hashlib.sha256(raw).hexdigest()
    with erp.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            row, _ = shipment(db, identifier)
            require_stage(row, "packed", "dispatched")
            old = db.execute(
                "SELECT id FROM logistics_labels WHERE shipment_id=? AND sha256=?",
                (identifier, digest),
            ).fetchone()
            if old:
                return {"id": old[0], "duplicate": True}
            label_id, now = str(uuid4()), erp.now()
            db.execute(
                "INSERT INTO logistics_labels VALUES (?,?,?,?,?,?,?)",
                (label_id, identifier, format_, digest, raw, "operator_upload_unverified", now),
            )
            # Content deliberately excluded from the audit payload.
            erp.execute_in(
                db,
                "label-" + label_id,
                "logistics.label.record",
                {"id": identifier, "label_id": label_id},
                actor="importação de documento local",
            )
            db.commit()
            return {"id": label_id, "duplicate": False, "verified": False}
        except Exception:
            db.rollback()
            raise


def snapshot(db):
    shipments = []
    for row in db.execute("SELECT * FROM logistics_shipments ORDER BY created_at DESC,id"):
        data = json.loads(row["data"])
        shipments.append(
            {
                **dict(row),
                "data": data,
                "scanned": {s: str(q) for s, q in scanned(db, row["id"], data["cycle"]).items()},
            }
        )
    return {
        "shipments": shipments,
        "manifests": [
            {**dict(r), "data": json.loads(r["data"])}
            for r in db.execute("SELECT * FROM logistics_manifests ORDER BY created_at DESC")
        ],
        "labels": [
            dict(r)
            for r in db.execute(
                "SELECT id,shipment_id,format,sha256,provenance,created_at "
                "FROM logistics_labels ORDER BY created_at DESC"
            )
        ],
        "tracking_events": [
            dict(r)
            for r in db.execute("SELECT * FROM logistics_tracking ORDER BY seq DESC LIMIT 200")
        ],
        "external_writes": False,
        "mode": "local_only",
    }
