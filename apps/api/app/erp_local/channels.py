"""Durable normalized event intake and stock previews. There is NO HTTP sender here.

REA established client-visible effects, not Bling's internal queue implementation.
This adapter supplies those effects locally. Provider authentication, subscriptions,
store policies and publication authority are separate, still disabled concerns.
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal

from app.erp_local.core import ERPError, dumps, number, text


def timestamp(value):
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError
        return stamp.astimezone(UTC)
    except (ValueError, TypeError, AttributeError):
        raise ERPError("invalid_event_time", "O evento precisa de data e fuso horário.") from None


def normalize_event(raw):
    fields = {
        "channel",
        "store",
        "event_id",
        "external_id",
        "event_type",
        "occurred_at",
        "order_created_at",
        "depot",
        "items",
    }
    if not isinstance(raw, dict) or set(raw) - fields:
        raise ERPError("invalid_channel_event", "Campos do evento fora do formato local.")
    result = {
        k: text(raw.get(k), k)
        for k in ("channel", "store", "event_id", "external_id", "event_type", "depot")
    }
    if result["event_type"] not in {"order.confirmed", "order.cancelled", "order.shipped"}:
        raise ERPError("unsupported_event", "Essa situação exige um mapeamento próprio do canal.")
    created, occurred = timestamp(raw.get("order_created_at")), timestamp(raw.get("occurred_at"))
    if occurred < created or occurred > datetime.now(UTC) + timedelta(minutes=5):
        raise ERPError("invalid_event_time", "A sequência de datas do evento é inválida.")
    result.update(order_created_at=created.isoformat(), occurred_at=occurred.isoformat())
    items = raw.get("items", [])
    if not isinstance(items, list) or len(items) > 200:
        raise ERPError("invalid_items", "Itens do evento inválidos.")
    if result["event_type"] == "order.confirmed" and not items:
        raise ERPError("invalid_items", "A confirmação precisa dos itens completos.")
    result["items"] = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {"sku", "quantity", "unit_price"}:
            raise ERPError("invalid_items", "Campos dos itens fora do formato local.")
        result["items"].append(
            {
                "sku": text(item["sku"], "SKU"),
                "quantity": str(number(item["quantity"])),
                "unit_price": str(number(item["unit_price"], unit=True, zero=True)),
            }
        )
    return result


def preview_quantity(erp, db, sku, depot):
    from app.erp_local.deposits import metadata
    from app.erp_local.product_details import details

    try:
        settings = metadata(db, depot)
    except ERPError:
        return None, "depot_review"
    if settings["status"] != "active" or settings["purpose"] == "quarantine":
        return None, "depot_unavailable"
    if details(db, sku)["status"] != "active":
        # An inactive or excluded product promises nothing: its listings go to 0.
        return "0", "product_inactive"
    product = erp.product(db, sku)
    if product["kind"] == "service":
        return None, "product_service"
    origin = erp.origin(db, sku)
    if origin and origin["catalog_state"] != "ready":
        return None, "catalog_review"
    parts = (
        json.loads(product["components"])
        if product["kind"] == "kit"
        else [{"sku": sku, "quantity": "1"}]
    )
    if not parts:
        return None, "missing_structure"
    values = []
    for part in parts:
        component = erp.origin(db, part["sku"])
        if component and (
            component["stock_state"] != "known" or component["catalog_state"] != "ready"
        ):
            return None, "stock_review"
        physical, reserved = erp.balance(db, part["sku"], depot)
        values.append((physical - reserved) / Decimal(part["quantity"]))
    # Sellable whole-unit preview. Channel-specific policies still require validation.
    quantity = max(Decimal(0), min(values).to_integral_value(rounding=ROUND_FLOOR))
    return str(quantity), "store_policy_pending"


def capture_stock_changes(erp, db, key, *, changed_skus=()):
    from app.erp_local.integration_registry import policy

    changed = set(changed_skus) | {
        r[0] for r in db.execute("SELECT DISTINCT sku FROM movements WHERE operation_key=?", (key,))
    }
    if not changed:
        return
    for row in db.execute("SELECT sku,components FROM products WHERE kind='kit'"):
        if any(p["sku"] in changed for p in json.loads(row["components"])):
            changed.add(row["sku"])
    cache = {}
    for sku in sorted(changed):
        for route in db.execute("SELECT * FROM stock_routes WHERE sku=? AND active=1", (sku,)):
            pair = (sku, route["depot"])
            if pair not in cache:
                cache[pair] = preview_quantity(erp, db, *pair)
            quantity, reason = cache[pair]
            account_policy = policy(db, route["channel"], route["store"])
            blocked_by_policy = False
            if account_policy:
                if account_policy["status"] != "shadow":
                    reason, blocked_by_policy = "store_policy_review", True
                elif account_policy["source_stock_enabled"] is False:
                    reason, blocked_by_policy = "source_stock_disabled", True
            db.execute(
                "UPDATE stock_intents SET status='superseded' WHERE route_id=? "
                "AND status IN ('preview','blocked')",
                (route["id"],),
            )
            db.execute(
                "INSERT INTO stock_intents(route_id,operation_key,quantity,status,reason,"
                "created_at) "
                "VALUES (?,?,?,?,?,?)",
                (
                    route["id"],
                    key,
                    quantity,
                    "preview" if quantity is not None and not blocked_by_policy else "blocked",
                    reason,
                    erp.now(),
                ),
            )


class ChannelFlow:
    def __init__(self, erp):
        self.erp = erp

    def receive(self, raw, *, source="simulation"):
        if source not in {"simulation", "provider_read"}:
            raise ERPError("invalid_event_source", "Origem do evento inválida.")
        payload = normalize_event(raw)
        fingerprint = hashlib.sha256(dumps([source, payload]).encode()).hexdigest()
        with self.erp.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT * FROM channel_inbox WHERE channel=? AND store=? AND event_id=?",
                (payload["channel"], payload["store"], payload["event_id"]),
            ).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise ERPError("event_conflict", "Esse evento já chegou com outros dados.", 409)
                return {**self.row(previous), "replayed": True}
            cursor = db.execute(
                "INSERT INTO channel_inbox(channel,store,event_id,external_id,fingerprint,"
                "payload,source,status,received_at) VALUES (?,?,?,?,?,?,?,'received',?)",
                (
                    payload["channel"],
                    payload["store"],
                    payload["event_id"],
                    payload["external_id"],
                    fingerprint,
                    dumps(payload),
                    source,
                    self.erp.now(),
                ),
            )
            identifier = cursor.lastrowid
            # Receipt is durable before processing; a crash leaves it available for recovery.
            db.commit()
        self.process(identifier)
        self.recover(only_deferred=True)
        with self.erp.connection() as db:
            return {
                **self.row(
                    db.execute("SELECT * FROM channel_inbox WHERE id=?", (identifier,)).fetchone()
                ),
                "replayed": False,
            }

    @staticmethod
    def row(row):
        value = dict(row)
        value.pop("fingerprint")
        value["payload"] = json.loads(value["payload"])
        value["outcome"] = json.loads(value["outcome"]) if value["outcome"] else None
        return value

    def process(self, identifier):
        with self.erp.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM channel_inbox WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise ERPError("event_not_found", "Evento não encontrado.", 404)
            if row["status"] in {"processed", "ignored"}:
                return
            db.execute("SAVEPOINT effect")
            try:
                status, result = self.apply(db, row)
                db.execute("RELEASE effect")
            except ERPError as exc:
                db.execute("ROLLBACK TO effect")
                db.execute("RELEASE effect")
                status = "deferred" if exc.code == "order_not_received" else "review"
                result = {"code": exc.code, "message": exc.message}
            db.execute(
                "UPDATE channel_inbox SET status=?,outcome=?,attempts=attempts+1 WHERE id=?",
                (status, dumps(result), identifier),
            )
            db.commit()

    def apply(self, db, row):
        from app.erp_local.integration_registry import cancellation_gate, policy

        p = json.loads(row["payload"])
        imported = db.execute("SELECT data FROM catalog_import WHERE id=1").fetchone()
        if imported and timestamp(p["order_created_at"]) <= timestamp(
            json.loads(imported[0])["loaded_at"]
        ):
            raise ERPError(
                "opening_reservation_review",
                "Pedido anterior à abertura: concilie a reserva histórica antes de aplicar.",
                409,
            )
        identity = (p["channel"], p["store"], p["external_id"])
        watermark = db.execute(
            "SELECT * FROM channel_watermarks WHERE channel=? AND store=? AND external_id=?",
            identity,
        ).fetchone()
        if watermark:
            if timestamp(p["occurred_at"]) < timestamp(watermark["occurred_at"]):
                return "ignored", {"code": "stale_event"}
            if (
                watermark["state"] == "order.cancellation_review"
                and p["event_type"] == "order.confirmed"
            ):
                return "review", {
                    "code": "cancellation_review_pending",
                    "message": "Há cancelamento aguardando conferência nesta conta.",
                }
            if (
                watermark["state"] in {"order.cancelled", "order.shipped"}
                and p["event_type"] == "order.confirmed"
            ):
                return "ignored", {"code": "terminal_order"}
            if (
                p["occurred_at"] == watermark["occurred_at"]
                and p["event_type"] != watermark["state"]
                and {p["event_type"], watermark["state"]}
                != {"order.cancelled", "order.cancellation_review"}
            ):
                raise ERPError(
                    "ambiguous_sequence",
                    "Situações diferentes têm a mesma data; confira no canal.",
                    409,
                )
        key = "inbox-" + str(row["id"])
        doc = db.execute(
            "SELECT id,status FROM documents WHERE external_key=?", (dumps(list(identity)),)
        ).fetchone()
        kind = p["event_type"]
        if row["source"] == "provider_read":
            account_policy = policy(db, p["channel"], p["store"])
            if (
                kind == "order.confirmed"
                and account_policy
                and (
                    account_policy["status"] != "shadow"
                    or account_policy["automatic_orders_observed"] is False
                )
            ):
                raise ERPError(
                    "store_policy_review",
                    "Confira a identidade e a regra de importação desta conta.",
                    409,
                )
            if kind == "order.cancelled" and not cancellation_gate(db, p):
                db.execute(
                    "INSERT INTO channel_watermarks VALUES (?,?,?,?,?) "
                    "ON CONFLICT(channel,store,external_id) DO UPDATE SET "
                    "occurred_at=excluded.occurred_at,state=excluded.state",
                    (*identity, p["occurred_at"], "order.cancellation_review"),
                )
                return "review", {
                    "code": "cancellation_policy_review",
                    "message": "Cancelamento exige conferência conforme a regra da conta. "
                    "A reserva foi preservada.",
                }
        result = {"code": "cancellation_recorded_without_reservation"}
        if kind == "order.confirmed":
            for sku in self.erp.requirements(db, self.erp.items(db, p["items"])):
                origin = self.erp.origin(db, sku) or {}
                if origin.get("stock_recovery_at") and timestamp(
                    p["order_created_at"]
                ) <= timestamp(origin["stock_recovery_at"]):
                    raise ERPError(
                        "opening_reservation_review",
                        "O pedido antecede o complemento de abertura de " + sku,
                        409,
                    )
            result = self.erp.execute_in(
                db,
                key,
                "order.import",
                {k: p[k] for k in ("channel", "store", "external_id", "depot", "items")},
                "entrada " + row["source"],
            )
        elif doc:
            result = self.erp.execute_in(
                db,
                key,
                "order.cancel" if kind == "order.cancelled" else "order.ship",
                {"id": doc["id"]},
                "entrada " + row["source"],
            )
        elif kind == "order.shipped":
            raise ERPError("order_not_received", "O despacho aguarda a confirmação do pedido.", 409)
        db.execute(
            "INSERT INTO channel_watermarks VALUES (?,?,?,?,?) "
            "ON CONFLICT(channel,store,external_id) "
            "DO UPDATE SET occurred_at=excluded.occurred_at,state=excluded.state",
            (*identity, p["occurred_at"], kind),
        )
        return "processed", result

    def recover(self, *, only_deferred=False):
        with self.erp.connection() as db:
            rows = db.execute(
                "SELECT id FROM channel_inbox WHERE status IN ('received','deferred') "
                "OR (status='review' AND ?=1) ORDER BY id LIMIT 100",
                (0 if only_deferred else 1,),
            ).fetchall()
        for row in rows:
            self.process(row["id"])
        return {"examined": len(rows), "external_writes": False}

    def prepare_routes(self, reference):
        metadata = reference.info()["snapshot"]
        if not metadata:
            raise ERPError("reference_required", "Carregue a referência primeiro.")
        integrations = {r["id"]: r for r in reference.all_rows("integrations")}
        links = reference.all_rows("links")
        with self.erp.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self.erp.depot(db, "geral")
            origins = {
                json.loads(r["data"])["source_id"]: r["sku"]
                for r in db.execute("SELECT * FROM catalog_origin")
            }
            key = "routes-" + metadata["digest"]
            previous = db.execute(
                "SELECT result FROM events WHERE operation_key=?", (key,)
            ).fetchone()
            if previous:
                return {**json.loads(previous[0]), "replayed": True}
            old_routes = {
                r["id"]: (
                    r["sku"],
                    r["channel"],
                    r["store"],
                    r["external_id"],
                    r["variation_id"],
                    r["depot"],
                )
                for r in db.execute("SELECT * FROM stock_routes")
            }
            changed_routes = []
            db.execute("UPDATE stock_routes SET active=0")
            count = skipped = 0
            candidates = {}
            for link in links:
                integration = integrations.get(link["integration_id"])
                sku = origins.get(link["product_id"])
                if (
                    not sku
                    or not integration
                    or integration["archived_at"]
                    or integration["status"] != "active"
                    or link["platform"] == "bling"
                    or link["platform"] != integration["platform"]
                    or not link["external_id"]
                ):
                    skipped += 1
                    continue
                target = (
                    link["platform"],
                    integration["id"],
                    link["external_id"],
                    str(link["variation_id"]) if link["variation_id"] is not None else "",
                )
                candidates.setdefault(target, []).append((link, sku, integration))
            ambiguous = 0
            for matches in candidates.values():
                if len({match[1] for match in matches}) != 1:
                    ambiguous += 1
                    skipped += len(matches)
                    continue
                skipped += len(matches) - 1
                link, sku, integration = sorted(matches, key=lambda match: match[0]["id"])[0]
                current = (
                    sku,
                    link["platform"],
                    integration["id"],
                    link["external_id"],
                    str(link["variation_id"]) if link["variation_id"] is not None else None,
                    "geral",
                )
                if link["id"] in old_routes and current != old_routes[link["id"]]:
                    changed_routes.append((link["id"],))
                db.execute(
                    "INSERT INTO stock_routes VALUES (?,?,?,?,?,?,?,?,1,?) ON CONFLICT(id) "
                    "DO UPDATE SET sku=excluded.sku,channel=excluded.channel,store=excluded.store,"
                    "store_name=excluded.store_name,external_id=excluded.external_id,variation_id=excluded.variation_id,"
                    "depot=excluded.depot,active=1,source_digest=excluded.source_digest",
                    (
                        link["id"],
                        sku,
                        link["platform"],
                        integration["id"],
                        integration["name"],
                        link["external_id"],
                        str(link["variation_id"]) if link["variation_id"] is not None else None,
                        "geral",
                        metadata["digest"],
                    ),
                )
                count += 1
            db.execute(
                "UPDATE stock_intents SET status='superseded' "
                "WHERE status IN ('preview','blocked') "
                "AND route_id IN (SELECT id FROM stock_routes WHERE active=0)"
            )
            db.executemany(
                "UPDATE stock_intents SET status='superseded' WHERE route_id=? "
                "AND status IN ('preview','blocked')",
                changed_routes,
            )
            result = {
                "routes": count,
                "skipped": skipped,
                "ambiguous_targets": ambiguous,
                "mode": "shadow",
                "external_writes": False,
                "source_digest": metadata["digest"],
            }
            db.execute(
                "INSERT INTO events(operation_key,fingerprint,kind,actor,payload,result,"
                "created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    key,
                    metadata["digest"],
                    "channels.routes.prepare",
                    "preparação local",
                    dumps({"source_digest": metadata["digest"]}),
                    dumps(result),
                    self.erp.now(),
                ),
            )
            db.commit()
            return {**result, "replayed": False}

    def snapshot(self):
        with self.erp.connection() as db:
            db.execute("BEGIN")
            return {
                "mode": "shadow",
                "external_writes": False,
                "transport_available": False,
                "provider_reads": [
                    json.loads(r[0])
                    for r in db.execute(
                        "SELECT result FROM events WHERE kind='channels.provider.read' "
                        "ORDER BY seq DESC LIMIT 10"
                    )
                ],
                "routes": db.execute("SELECT count(*) FROM stock_routes WHERE active=1").fetchone()[
                    0
                ],
                "stores": db.execute(
                    "SELECT count(DISTINCT store) FROM stock_routes WHERE active=1"
                ).fetchone()[0],
                "bindings": [
                    dict(r)
                    for r in db.execute(
                        "SELECT store AS id,channel,store_name AS name,count(*) AS routes "
                        "FROM stock_routes WHERE active=1 GROUP BY store,channel,store_name "
                        "ORDER BY channel,store_name"
                    )
                ],
                "inbox_counts": dict(
                    db.execute(
                        "SELECT status,count(*) FROM channel_inbox GROUP BY status"
                    ).fetchall()
                ),
                "intent_counts": dict(
                    db.execute(
                        "SELECT status,count(*) FROM stock_intents GROUP BY status"
                    ).fetchall()
                ),
                "inbox": [
                    self.row(r)
                    for r in db.execute("SELECT * FROM channel_inbox ORDER BY id DESC LIMIT 100")
                ],
                "intents": [
                    dict(r)
                    for r in db.execute(
                        "SELECT i.*,r.sku,r.channel,r.store_name,r.external_id,"
                        "r.variation_id FROM stock_intents i "
                        "JOIN stock_routes r ON r.id=i.route_id "
                        "ORDER BY i.id DESC LIMIT 100"
                    )
                ],
            }
