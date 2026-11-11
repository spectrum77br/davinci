"""Continuous order reception per account: cursor, retries and lookback, off until enabled.

Nothing reads a store here unless that account was enabled and a provider was handed in. Reads
stay observations. The cursor only moves after a whole window was read, and windows overlap
because stores report updates late. Page tokens never leave the server: paging narrows the time
window using the last update already seen.
"""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.erp_local import native_orders, recipient_capture
from app.erp_local.core import ERPError, text

SCHEMA = """
CREATE TABLE IF NOT EXISTS ingest_accounts (
 channel TEXT NOT NULL, store TEXT NOT NULL, enabled INTEGER NOT NULL, authority TEXT NOT NULL,
 cursor TEXT, window_from TEXT, window_to TEXT, failures INTEGER NOT NULL,
 next_attempt_at TEXT, last_ok_at TEXT, last_state TEXT, lookback_at TEXT, lease_until TEXT,
 updated_at TEXT NOT NULL, PRIMARY KEY(channel,store));
CREATE TABLE IF NOT EXISTS ingest_runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, channel TEXT NOT NULL, store TEXT NOT NULL,
 kind TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, state TEXT,
 reads INTEGER NOT NULL, observed INTEGER NOT NULL, added INTEGER NOT NULL,
 date_from TEXT, date_to TEXT);
"""
COMMANDS = {"ingest.account.set"}
OVERLAP = timedelta(minutes=15)
SAFETY_LAG = timedelta(minutes=2)
FIRST_WINDOW = timedelta(days=1)
MAX_WINDOW = timedelta(hours=24)
LOOKBACK_EVERY = timedelta(hours=24)
LOOKBACK_WINDOW = timedelta(days=7)
# The first re-read of an account covers a month, so sales read before the reading gained a
# field (Mercado Livre coupon, TikTok shipping) get it too.
FIRST_LOOKBACK_WINDOW = timedelta(days=30)
# Shopee, Amazon, Carrefour and Magalu gained the re-read on 11/11/2026. Their first one covers the
# days before the reception ran on the server (04 to 09/11): sales never read and situations that
# changed meanwhile. Ten one-day reads per account keep that first round short.
CATCH_UP_LOOKBACK_WINDOW = timedelta(days=10)
LEASE = timedelta(minutes=10)
MAX_READS = 20
BACKOFF_BASE = 60
BACKOFF_CAP = 3600
# When Bling brings a sale in, from each store's own configuration there.
OPEN_STATUS = {
    "shopee": {"READY_TO_SHIP", "PROCESSED", "INVOICE_PENDING", "RETRY_SHIP"},
    "ml": {"PAID"},
    "tiktok": {"AWAITING_SHIPMENT", "AWAITING_COLLECTION"},
    "amazon": {"UNSHIPPED", "PARTIALLYSHIPPED", "PARTIALLY_SHIPPED"},
    "magalu": {"APPROVED"},
    "carrefour": {"SHIPPING"},
}
CANCELLED = {"CANCELLED", "CANCELED"}
VERIFY_CANCELLATION = 83962
PROMOTION_REFUSALS = {
    "cancelled": "Pedido cancelado na loja.",
    "not_open": "A loja ainda não liberou este pedido para envio.",
    "cart_waiting": "Carrinho ainda em leitura: aguarde a próxima rodada.",
    "no_items": "Pedido sem itens.",
    "item": "Item sem vínculo exato com o catálogo.",
    "quantity_unknown": "Quantidade não comprovada pela loja.",
    "price_unknown": "Preço não comprovado pela loja.",
}
NEEDS_PERSON = {
    "auth_denied",
    "credentials_missing",
    "configuration_missing",
    "credential_unreadable",
    "marketplace_mismatch",
}


def _iso(value):
    return value.astimezone(UTC).isoformat()


def _when(value):
    return datetime.fromisoformat(value) if value else None


def apply(erp, db, p):
    if not isinstance(p, dict) or set(p) != {"channel", "store", "enabled"}:
        raise ERPError("ingest_fields", "Informe canal, conta e situação da recepção.")
    channel, store = text(p["channel"], "canal"), text(p["store"], "conta")
    if type(p["enabled"]) is not bool:
        raise ERPError("ingest_fields", "Escolha ligar ou desligar a recepção.")
    if not db.execute(
        "SELECT 1 FROM store_policies WHERE channel=? AND store=?", (channel, store)
    ).fetchone():
        raise ERPError("ingest_store", "A conta precisa constar no registro próprio de lojas.", 409)
    db.execute(
        "INSERT INTO ingest_accounts(channel,store,enabled,authority,failures,updated_at) "
        "VALUES (?,?,?,'bling',0,?) ON CONFLICT(channel,store) DO UPDATE SET "
        "enabled=excluded.enabled,failures=0,next_attempt_at=NULL,updated_at=excluded.updated_at",
        (channel, store, int(p["enabled"]), erp.now()),
    )
    return {"channel": channel, "store": store, "enabled": p["enabled"], "external_writes": False}


def _account(db, channel, store):
    row = db.execute(
        "SELECT * FROM ingest_accounts WHERE channel=? AND store=?", (channel, store)
    ).fetchone()
    if not row:
        raise ERPError("ingest_store", "Conta sem recepção configurada.", 404)
    return row


def _take_lease(erp, channel, store, now):
    with erp.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _account(db, channel, store)
        lease = _when(row["lease_until"])
        if lease and lease > now:
            db.rollback()
            return None
        db.execute(
            "UPDATE ingest_accounts SET lease_until=? WHERE channel=? AND store=?",
            (_iso(now + LEASE), channel, store),
        )
        db.commit()
        return dict(row)


def _save(erp, channel, store, **fields):
    names = ",".join(f"{k}=?" for k in fields)
    with erp.connection() as db:
        db.execute(
            f"UPDATE ingest_accounts SET {names},updated_at=? WHERE channel=? AND store=?",  # noqa: S608
            (*fields.values(), erp.now(), channel, store),
        )


def _record(erp, channel, store, kind, started, state, reads, observed, added, start, end):
    with erp.connection() as db:
        db.execute(
            "INSERT INTO ingest_runs(channel,store,kind,started_at,finished_at,state,reads,"
            "observed,added,date_from,date_to) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                channel,
                store,
                kind,
                _iso(started),
                erp.now(),
                state,
                reads,
                observed,
                added,
                _iso(start),
                _iso(end),
            ),
        )


def _backoff(failures, retry_after=None):
    wait = min(BACKOFF_BASE * 2 ** max(failures - 1, 0), BACKOFF_CAP)
    return timedelta(seconds=max(wait, retry_after or 0))


def _read_window(erp, channel, store, start, end, provider, importer, *, partial_ok=False):
    """Read until the window is complete. Returns (state, cursor to resume from, totals).

    `partial_ok` is for re-reads only, which never move the cursor: what a partial reading
    brought is kept (observations are keyed by content), and the window counts as read.
    """
    reads = observed = added = 0
    while reads < MAX_READS:
        read = provider(channel=channel, store=store, date_from=start, date_to=end)
        reads += 1
        account = next(a for a in read["accounts"] if a["integration_id"] == store)
        if partial_ok and account["state"] == "partial_read" and account.get("orders"):
            result = importer(erp, read)
            observed += len(account["orders"])
            added += 0 if result.get("replayed") else result.get("observations_added", 0)
            return "partial_kept", end, (reads, observed, added), None
        if account["state"] != "read_ok":
            return account["state"], start, (reads, observed, added), account.get("retry_after")
        result = importer(erp, read)
        orders = account["orders"]
        observed += len(orders)
        # A replayed import reports what the first one added; it added nothing now.
        added += 0 if result.get("replayed") else result.get("observations_added", 0)
        if account["has_more"] is None:
            return "window_unproven", start, (reads, observed, added), None
        if not account["has_more"]:
            return "complete", end, (reads, observed, added), None
        latest = max((_when(o["updated_at"]) for o in orders), default=None)
        if not latest or latest <= start:
            return "stuck_page", start, (reads, observed, added), None
        start = latest
    return "partial", start, (reads, observed, added), None


def run_account(erp, channel, store, provider, *, now=None, importer=None):
    """One reception round. Any refusal leaves the cursor where it was."""
    now = now or datetime.now(UTC)
    importer = importer or native_orders.import_read
    with erp.connection() as db:
        row = _account(db, channel, store)
    if not row["enabled"]:
        return {"state": "disabled"}
    retry_at = _when(row["next_attempt_at"])
    if retry_at and retry_at > now:
        return {"state": "waiting", "next_attempt_at": row["next_attempt_at"]}
    row = _take_lease(erp, channel, store, now)
    if row is None:
        return {"state": "busy"}
    cursor = _when(row["cursor"])
    start = _when(row["window_from"]) or ((cursor - OVERLAP) if cursor else now - FIRST_WINDOW)
    end = _when(row["window_to"]) or min(now - SAFETY_LAG, start + MAX_WINDOW)
    state, position, (reads, observed, added), retry_after = (
        "provider_unavailable",
        start,
        (
            0,
            0,
            0,
        ),
        None,
    )
    try:
        state, position, (reads, observed, added), retry_after = _read_window(
            erp, channel, store, start, end, provider, importer
        )
    except (ERPError, ValueError, KeyError, TypeError, StopIteration, OSError):
        state = "invalid_response"
    fields = {"last_state": state, "lease_until": None}
    if state == "complete":
        fields.update(
            cursor=_iso(end),
            window_from=None,
            window_to=None,
            failures=0,
            next_attempt_at=None,
            last_ok_at=_iso(now),
        )
    elif state == "partial":
        # Keep the fixed end of the window so the next round finishes the same one.
        fields.update(window_from=_iso(position), window_to=_iso(end))
    else:
        failures = row["failures"] + 1
        fields.update(
            failures=failures,
            window_from=_iso(position),
            window_to=_iso(end),
            next_attempt_at=_iso(now + _backoff(failures, retry_after)),
        )
        if state in NEEDS_PERSON:
            fields.update(enabled=0)
    _save(erp, channel, store, **fields)
    _record(erp, channel, store, "cursor", now, state, reads, observed, added, start, end)
    return {"state": state, "reads": reads, "observed": observed, "added": added}


def first_lookback(channel):
    return FIRST_LOOKBACK_WINDOW if channel in CHARGED else CATCH_UP_LOOKBACK_WINDOW


def run_lookback(erp, channel, store, provider, *, now=None, importer=None):
    """Periodic re-read of recent days. Finds late changes and never moves the cursor."""
    now = now or datetime.now(UTC)
    importer = importer or native_orders.import_read
    with erp.connection() as db:
        row = _account(db, channel, store)
    done = _when(row["lookback_at"])
    if not row["enabled"] or (done and now - done < LOOKBACK_EVERY):
        return {"state": "not_due"}
    with erp.connection() as db:
        first = not db.execute(
            "SELECT 1 FROM ingest_runs WHERE channel=? AND store=? AND kind='lookback' "
            "AND state IN ('complete','partial_kept') LIMIT 1",
            (channel, store),
        ).fetchone()
    start, end = now - (first_lookback(channel) if first else LOOKBACK_WINDOW), now - SAFETY_LAG
    # One day per read, like the reception: the store readers refuse longer windows.
    state, reads, observed, added, position, kept = "complete", 0, 0, 0, start, False
    while position < end:
        stop = min(position + MAX_WINDOW, end)
        try:
            state, _, (r, o, a), _ = _read_window(
                erp, channel, store, position, stop, provider, importer, partial_ok=True
            )
        except (ERPError, ValueError, KeyError, TypeError, StopIteration, OSError):
            state, r, o, a = "invalid_response", 0, 0, 0
        reads, observed, added = reads + r, observed + o, added + a
        kept = kept or state == "partial_kept"
        if state not in {"complete", "partial_kept"}:
            break
        position = stop
    if state in {"complete", "partial_kept"}:
        state = "partial_kept" if kept else "complete"
        _save(erp, channel, store, lookback_at=_iso(now))
    elif first:
        # A failed first re-read waits an hour instead of starting over every round.
        _save(erp, channel, store, lookback_at=_iso(now - LOOKBACK_EVERY + timedelta(hours=1)))
    _record(erp, channel, store, "lookback", now, state, reads, observed, added, start, end)
    return {"state": state, "reads": reads, "observed": observed, "added": added}


def run_due(erp, providers, *, now=None):
    """One pass over enabled accounts. One account failing never stops the others."""
    now = now or datetime.now(UTC)
    with erp.connection() as db:
        rows = db.execute(
            "SELECT channel,store FROM ingest_accounts WHERE enabled=1 ORDER BY channel,store"
        ).fetchall()
    results = {}
    for row in rows:
        provider = providers.get(row["channel"])
        if provider:
            results[(row["channel"], row["store"])] = run_account(
                erp, row["channel"], row["store"], provider, now=now
            )
    return results


def _units(db, channel, store):
    """Orders as the store sells them: a Mercado Livre cart is one sale, under its own number."""
    latest = {}
    for row in db.execute(
        "SELECT external_id,updated_at,data FROM native_order_observations "
        "WHERE channel=? AND store=? ORDER BY updated_at",
        (channel, store),
    ):
        latest[row["external_id"]] = json.loads(row["data"])
    units = {}
    for external, order in latest.items():
        units.setdefault(str(order.get("pack_id") or order.get("cart_id") or external), {})[
            external
        ] = order
    return units


def _is_cancelled(order):
    return str(order.get("status") or "").upper() in CANCELLED


def _settled(account, orders):
    """A cart is complete once the reading has moved past its newest order."""
    cursor = _when(account["cursor"])
    newest = max((_when(o.get("created_at")) for o in orders if o.get("created_at")), default=None)
    return bool(cursor and newest and cursor > newest)


def _resolve(db, store, item, channel):
    """The seller's own code when it is exactly a product, as Bling does; else the listing link.

    On 09/11/2026 the code the store sends matched Bling's item family in 607 of 674 Shopee sales
    against 562 through DaVinci's listing links (links go stale when a variation's code changes,
    e.g. a phone sold "AVULSO" still linked to its kit with earphones), and made none worse.
    """
    resolved = native_orders.resolve_item(db, store, item, channel=channel)
    if not item.get("seller_sku"):
        return resolved
    row = db.execute("SELECT sku,kind FROM products WHERE sku=?", (item["seller_sku"],)).fetchone()
    if not row or (resolved["state"] == "matched" and resolved.get("sku") == row["sku"]):
        return resolved
    origin = db.execute("SELECT data FROM catalog_origin WHERE sku=?", (row["sku"],)).fetchone()
    ready = not origin or json.loads(origin[0])["catalog_state"] == "ready"
    return {
        **resolved,
        "sku": row["sku"],
        "kind": row["kind"],
        "state": "matched" if ready else "catalog_review",
    }


def _quantity(channel, item):
    """TikTok sends one line per unit: 91 of 91 lines matched Bling's quantity on 06/11/2026."""
    quantity = item.get("quantity")
    return 1 if quantity is None and channel == "tiktok" else quantity


def _price(item):
    return item.get("unit_price") or item.get("sale_price")


def ml_charges(db, store, key, orders):
    """Mercado Livre: Bling's total is the products, plus the freight the buyer paid, minus the
    seller's share of the coupons (37 of 37 sales checked on 09/11/2026). Both are read apart
    from the order: freight from the shipment, coupons from /orders/{id}/discounts; None while
    either is unread."""
    row = db.execute(
        "SELECT data FROM store_tracking WHERE channel='ml' AND store=? AND number=?",
        (store, key),
    ).fetchone()
    data = json.loads(row["data"]) if row else {}
    if data.get("cost") in (None, "") or data.get("seller_coupon") in (None, ""):
        return None
    return {"freight": Decimal(str(data["cost"])), "coupon": Decimal(str(data["seller_coupon"]))}


def store_charges(db, channel, store, key, orders):
    """Freight and seller coupon that Bling adds to the products, per store, or None if unread.

    Mercado Livre: freight from the shipment, coupon from the order. TikTok: the buyer's
    shipping, without the handling fee TikTok puts in its own total.
    """
    if channel == "ml":
        return ml_charges(db, store, key, orders)
    if channel == "tiktok":
        if any("shipping_fee" not in order for order in orders):
            return None
        freight = sum(
            (Decimal(str((o["shipping_fee"] or {}).get("amount") or 0)) for o in orders),
            Decimal(0),
        )
        return {"freight": freight, "coupon": Decimal(0)}
    return None


CHARGED = {"ml", "tiktok"}


def _value_adjustment(orders, items, charges=None):
    """Discount or freight that takes the items to the total the store reports for the order.

    That header total equals Bling's on 94% to 100% of Shopee, TikTok, Amazon and Magalu sales.
    Mercado Livre passes its freight and coupon instead (see `ml_charges`).
    """
    if charges is not None:
        result = {}
        if charges["freight"] > 0:
            result["freight"] = str(charges["freight"].quantize(Decimal("0.01")))
        if charges["coupon"] > 0:
            result["discount"] = str(charges["coupon"].quantize(Decimal("0.01")))
        return result
    headers = [(order.get("paid") or order.get("total") or {}).get("amount") for order in orders]
    if not headers or any(amount is None for amount in headers):
        return {}
    gap = sum((Decimal(str(amount)) for amount in headers), Decimal(0)) - sum(
        (Decimal(i["quantity"]) * Decimal(i["unit_price"]) for i in items), Decimal(0)
    )
    gap = gap.quantize(Decimal("0.01"))
    if gap < 0:
        return {"discount": str(-gap)}
    return {"freight": str(gap)} if gap > 0 else {}


def _unit_reasons(db, channel, store, account, key, unit):
    orders = list(unit.values())
    live = [o for o in orders if not _is_cancelled(o)]
    if not live:
        return {"cancelled"}
    reasons = set()
    allowed = OPEN_STATUS.get(channel)
    if allowed is None or any(str(o.get("status") or "").upper() not in allowed for o in live):
        reasons.add("not_open")
    elif channel == "ml":
        from app.erp_local import ml_shipping

        if ml_shipping.left(db, store, key):
            reasons.add("not_open")
    if channel in CHARGED and store_charges(db, channel, store, key, live) is None:
        reasons.add("total_unknown")
    if (live[0].get("pack_id") or live[0].get("cart_id")) and not _settled(account, orders):
        reasons.add("cart_waiting")
    items = [item for order in live for item in order.get("items") or []]
    if not items:
        reasons.add("no_items")
    for item in items:
        resolved = _resolve(db, store, item, channel)
        if resolved["state"] != "matched":
            reasons.add("item_" + resolved["state"])
        if _quantity(channel, item) in (None, 0):
            reasons.add("quantity_unknown")
        if _price(item) is None:
            reasons.add("price_unknown")
    if not _unit_recipient(db, channel, store, unit):
        reasons.add("recipient_missing")
    return reasons


def _unit_recipient(db, channel, store, unit):
    for external in unit:
        captured = recipient_capture.for_order(db, channel, store, external)
        if captured and captured["ready"]:
            return captured
    return None


def promotion_plan(db, channel, store):
    """What would become an own order. Only reports: Bling stays responsible until a switch."""
    account = _account(db, channel, store)
    units = _units(db, channel, store)
    report = {"ready": [], "blocked": {}, "observed": sum(len(u) for u in units.values())}
    report["sales"] = len(units)
    for key, unit in sorted(units.items()):
        reasons = _unit_reasons(db, channel, store, account, key, unit)
        if reasons:
            for reason in reasons:
                report["blocked"][reason] = report["blocked"].get(reason, 0) + 1
        else:
            report["ready"].append(key)
    return report


def sync_promoted(erp, channel, store):
    """Cancelled at the store: Bling flags it for a person, and stock stays until they decide."""
    from app.erp_local.bling_compat import _current_situation, _set_situation

    with erp.connection() as db:
        units = _units(db, channel, store)
        rows = db.execute(
            "SELECT * FROM documents WHERE kind='order' AND status IN ('draft','reserved') "
            "AND json_extract(data,'$.channel')=? AND json_extract(data,'$.store')=?",
            (channel, store),
        ).fetchall()
    flagged = []
    for row in rows:
        unit = units.get(str(json.loads(row["data"]).get("external_id")))
        if not unit or not any(_is_cancelled(order) for order in unit.values()):
            continue
        with erp.connection() as db:
            if _current_situation(db, row) == VERIFY_CANCELLATION:
                continue
            db.execute("BEGIN IMMEDIATE")
            _set_situation(erp, db, row, VERIFY_CANCELLATION)
            db.commit()
        flagged.append(row["id"])
    return {"flagged": flagged}


def service_round(erp, channel, store, provider, *, reader=None, now=None):
    """Read the store, keep the buyers it still shows and flag what was cancelled."""
    report = {"reception": run_account(erp, channel, store, provider, now=now)}
    # Daily re-read of recent days, every store: a sale the reception missed (the server was off,
    # a store refused a read) comes back, and so do situations and, on Mercado Livre and TikTok,
    # freight and coupons that change after the sale is placed.
    report["lookback"] = run_lookback(erp, channel, store, provider, now=now)
    if reader is not None:
        report["recipients"] = recipient_capture.read_due(erp, channel, store, reader, now=now)
    report["status"] = sync_promoted(erp, channel, store)
    return report


def snapshot(db):
    return {
        "accounts": [
            dict(r)
            for r in db.execute(
                "SELECT channel,store,enabled,authority,cursor,failures,next_attempt_at,"
                "last_ok_at,last_state,lookback_at FROM ingest_accounts ORDER BY channel,store"
            )
        ],
        "runs": [
            dict(r) for r in db.execute("SELECT * FROM ingest_runs ORDER BY id DESC LIMIT 100")
        ],
    }


def run_on_host(script, request, *, alias="davinci-prod", timeout=300):
    """Runs a read script where the credentials are. On the server itself (ERP_READ_TRANSPORT=local)
    it runs in this container; elsewhere it goes through SSH into the API container."""
    import base64
    import os
    import re
    import subprocess
    import sys

    encoded = base64.b64encode(json.dumps(request).encode()).decode()
    if os.environ.get("ERP_READ_TRANSPORT") == "local":
        command = [sys.executable, "-", encoded]
    else:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", alias):
            raise ERPError("ingest_source", "Alias SSH inválido.")
        # Fixed command and validated alias; no credentials cross this boundary.
        command = [
            "/usr/bin/ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=15",
            alias,
            "docker exec -i davinci-api-1 uv run python - " + encoded,
        ]
    # Locally the read runs beside DaVinci: lower priority, so DaVinci always gets the CPU first.
    nicer = (lambda: os.nice(10)) if os.environ.get("ERP_READ_TRANSPORT") == "local" else None
    done = subprocess.run(  # noqa: S603
        command, input=script, capture_output=True, timeout=timeout, preexec_fn=nicer
    )
    if done.returncode:
        raise OSError("leitura indisponível")
    return json.loads(done.stdout)


def ssh_provider(platform, alias="davinci-prod"):
    """Reads through the existing channel probe; tokens never leave the host that holds them."""
    from pathlib import Path

    script = (Path(__file__).parent / f"{platform}_order_probe.py").read_bytes()

    def read(*, channel, store, date_from, date_to):
        request = {
            "integration_ids": [store],
            "window": {"from": int(date_from.timestamp()), "to": int(date_to.timestamp())},
        }
        return run_on_host(script, request, alias=alias)

    return read


def set_authority(erp, channel, store, authority):
    """The store's orders become ours only on purpose: this also needs the server's switch."""
    import os

    if os.environ.get("ERP_ALLOW_AUTHORITY_SWITCH") != "1":
        raise ERPError(
            "ingest_authority_closed", "A troca de autoridade segue fechada neste ambiente.", 409
        )
    if authority not in {"bling", "own"}:
        raise ERPError("ingest_authority", "Escolha Bling ou ERP próprio.")
    _save(erp, channel, store, authority=authority)


def promote(erp, channel, store, external, *, depot):
    """Turn one reviewed sale into an own order. Refuses while Bling still owns the store."""
    with erp.connection() as db:
        account = _account(db, channel, store)
        if account["authority"] != "own":
            raise ERPError(
                "ingest_authority_closed", "O Bling segue responsável por esta loja.", 409
            )
        units = _units(db, channel, store)
        key = next((k for k, unit in units.items() if external == k or external in unit), None)
        if key is None:
            raise ERPError("ingest_order", "Pedido não observado nesta conta.", 404)
        unit = units[key]
        reasons = _unit_reasons(db, channel, store, account, key, unit)
        for reason, message in PROMOTION_REFUSALS.items():
            if any(r.startswith(reason) for r in reasons):
                raise ERPError("ingest_" + reason, message, 409)
        items = []
        for order in unit.values():
            if _is_cancelled(order):
                continue
            for item in order.get("items") or []:
                resolved = _resolve(db, store, item, channel)
                items.append(
                    {
                        "sku": resolved["sku"],
                        "quantity": str(_quantity(channel, item)),
                        "unit_price": str(_price(item)["amount"]),
                    }
                )
        captured = _unit_recipient(db, channel, store, unit)
    if not captured:
        with erp.connection() as db:
            partial = next(
                (
                    recipient_capture.for_order(db, channel, store, e)
                    for e in unit
                    if recipient_capture.for_order(db, channel, store, e)
                ),
                None,
            )
        missing = ", ".join((partial or {}).get("missing") or ["sem leitura da loja"])
        raise ERPError("ingest_recipient", f"Comprador incompleto: {missing}.", 409)
    payload = {
        "channel": channel,
        "store": store,
        "external_id": key,
        "depot": depot,
        "items": items,
        "contact_id": recipient_capture.contact_for(erp, captured),
        "recipient": recipient_capture.order_recipient(captured),
    }
    orders = list(unit.values())
    live = [o for o in orders if not _is_cancelled(o)]
    charges = None
    if channel in CHARGED:
        with erp.connection() as db:
            charges = store_charges(db, channel, store, key, live)
        if charges is None:
            raise ERPError("ingest_total", "Frete ou cupom da loja ainda não lidos.", 409)
    payload.update(_value_adjustment(live, items, charges))
    if any(
        o.get("logistic_type") in {"self_service", "flex"} or o.get("fulfillment") == "flex"
        for o in orders
    ):
        payload["fulfillment_mode"] = "flex"
    return erp.command(f"promote-v1:{channel}:{store}:{key}", "order.import", payload)
