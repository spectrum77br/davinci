"""Orders and buyers from UpSeller's export, so Temu and Amazon stop depending on Bling.

UpSeller already has the four Temu stores and the Amazon stores connected, with the buyer whole
(tested 27/11/2026 against DaVinci's copy of Bling: name, CPF, CEP, house number and district
equal on 3 Temu and 3 Amazon orders). A robot on another PC exports from UpSeller every few
minutes and sends the spreadsheet here (docs/robo-upseller/PROMPT_ROBO_UPSELLER.md); a person can
send the same file from Integrações when the robot is down.

- Temu: an order the ERP does not have comes in as a read of its own source, in the same stores
  the copy of Bling uses, so the two never make two sales of one order. While Bling still owns
  the stores, an order the copy already brought is only compared.
- Amazon: the orders come straight from Amazon; only the buyer comes from here.
- The buyer goes through recipient_capture, sealed like any other. One already captured (from
  Bling) is compared, field by field, never overwritten here.

The spreadsheet is read in memory and never written to disk; nothing personal is logged or
returned, only order numbers and field names.
"""

import hashlib
import io
import json
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from app.erp_local import bling_copy_order_probe, channel_recipients, recipient_capture
from app.erp_local.core import ERPError, dumps

SOURCE = "upseller_copy_v1_read"
LIMIT = 2000
MAX_BYTES = 10 * 1024 * 1024
BRT = ZoneInfo("America/Sao_Paulo")
PLATFORMS = {"temu": "Temu", "amazon": "Amazon"}
NUMBERS = {"temu": bling_copy_order_probe.NUMBERS["temu"], "amazon": recipient_capture.AMAZON_ID}
# UpSeller's store name → the ERP store. Temu: the stores of the copy of Bling (Bling store
# codes); Amazon: DaVinci's integrations. A name not here is matched by the store register's own
# name ("Temu Barbosa" ↔ "Temu Barbosa", "Amazon KFA" ↔ "kfa"), else it is reported, not guessed.
STORES = {
    "temu": {
        "temu barbosa": "205925390",
        "temu jlas": "206288651",
        "temu atv": "206288703",
        "temu eron": "206288707",
    },
    "amazon": {
        "amazon kfa": "0b18f356-234c-42f2-8641-ffe254a6b69c",
        "amazon kia": "bee13a03-a109-4305-acd2-d5ac32ee64ea",
        "amazon poofy": "45790f1b-3972-48db-a43b-ca8ac13efbf8",
        "amazon vr": "049acd9a-3b98-479c-8419-fbffbf574cd0",
        "amazon nexus": "e5d6306b-d18f-47fe-af02-54cd26c5361d",
    },
}
ORDER = "Nº de Pedido da Plataforma"
REQUIRED = {
    ORDER,
    "Plataformas",
    "Nome da Loja no UpSeller",
    "Estado do Pedido",
    "Hora do Pedido",
    "Nome do Destinatário",
    "ID da Taxa",
    "Endereço 1",
    "Endereço 2",
    "Número",
    "Bairro",
    "Cidade",
    "Estado",
    "CEP",
}
REQUIRED_ORDERS = {"SKU (Armazém)", "Quantidade de Produtos", "Valor do Pedido"}
TIMES = (
    "Hora do Pedido",
    "Hora do Pagamento",
    "Horário de Saída",
    "Hora de Envio",
    "Horário da Retirada",
)
COMPLEMENT = re.compile(
    r"\b(ap|apt|apto|apartamento|casa|cs|bl|bloco|sala|sl|lote|lt|quadra|qd|fundos|fds|"
    r"andar|conj|conjunto|cond|condominio|km|loja|galpao|box|torre|edificio|ed)\b",
    re.IGNORECASE,
)
SCHEMA = """
CREATE TABLE IF NOT EXISTS upseller_imports (
 id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL, imported_at TEXT NOT NULL,
 summary TEXT NOT NULL);
"""
KEEP_IMPORTS = 500


def _text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _key(value):
    plain = unicodedata.normalize("NFKD", _text(value)).encode("ascii", "ignore").decode()
    return plain.casefold()


def _money(value):
    if value in (None, ""):
        return Decimal("0.00")
    if isinstance(value, int | float | Decimal):
        number = Decimal(str(value))
    else:
        text = re.sub(r"[^\d,.\-]", "", str(value))
        if "," in text:
            text = text.replace(".", "").replace(",", ".")
        try:
            number = Decimal(text or "0")
        except InvalidOperation:
            raise ERPError("upseller_value", "Valor que não se lê na planilha.") from None
    return number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _amount(value):
    return {"amount": f"{value:.2f}", "currency": "BRL"}


def _moment(value):
    """UpSeller writes Brazil's time, as a date cell or as text."""
    if isinstance(value, datetime):
        moment = value
    else:
        text = _text(value)
        if not text or text in {"-", "--"}:
            return None
        moment = None
        for pattern in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                moment = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
        if moment is None:
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=BRT)
    return moment.astimezone(UTC).replace(microsecond=0)


def read_sheet(content):
    """The export's rows as dicts by column name (UpSeller may reorder columns, never rename)."""
    from openpyxl import load_workbook

    if not content or len(content) > MAX_BYTES:
        raise ERPError("upseller_file", "Envie a planilha exportada do UpSeller (até 10 MB).")
    try:
        # Not read_only: UpSeller's file has no dimensions and would read as the header alone.
        sheet = load_workbook(io.BytesIO(content), data_only=True).worksheets[0]
    except Exception:  # noqa: BLE001 - any unreadable file is the same answer
        raise ERPError("upseller_file", "A planilha do UpSeller não abriu.") from None
    rows = sheet.iter_rows(values_only=True)
    head = [_text(h) for h in next(rows, ())]
    missing = sorted(REQUIRED - set(head))
    if missing:
        raise ERPError(
            "upseller_columns", "Faltam colunas na exportação do UpSeller: " + ", ".join(missing)
        )
    out = []
    for row in rows:
        if not any(c not in (None, "") for c in row):
            continue
        out.append({name: row[i] if i < len(row) else None for i, name in enumerate(head) if name})
    return head, out


def split_address(platform, row):
    """Street, number, complement and district apart, as the invoice needs them.

    Temu: the district comes in its column; the number column is masked, the number is in
    "Endereço 1" ("Rua X, 123"). Amazon: number and district columns come empty; the number is in
    "Endereço 1" or alone in "Endereço 2", and the district in the address lines.
    """
    line1, line2 = _text(row.get("Endereço 1")), _text(row.get("Endereço 2"))
    given_number = _text(row.get("Número"))
    district = _text(row.get("Bairro"))
    complement_parts = []
    street, number = line1, ""
    if (
        given_number
        and "*" not in given_number
        and re.fullmatch(r"\d{1,6}[A-Za-z]?|S/?N", given_number, re.I)
    ):
        number = given_number.upper() if given_number.casefold() in {"s/n", "sn"} else given_number
    # "Rua X, 123 - Centro" / "Rua X, 123, apto 4"
    if not district and " - " in street:
        street, _, tail = street.rpartition(" - ")
        if tail and not re.search(r"\d", tail) and not COMPLEMENT.search(tail):
            district = tail
        else:
            street = f"{street} - {tail}"
    found = re.search(r",\s*(\d{1,6}[A-Za-z]?)\b(.*)$", street)
    if found:
        if not number:
            number = found.group(1)
        rest = found.group(2).strip(" ,-")
        street = street[: found.start()].strip(" ,-")
        if rest:
            complement_parts.append(rest)
    elif not number:
        trailing = re.search(r"\s(\d{1,6}[A-Za-z]?)$", street)
        if trailing and re.search(r"[A-Za-z]", street[: trailing.start()]):
            number = trailing.group(1)
            street = street[: trailing.start()].strip(" ,-")
    if line2:
        if re.fullmatch(r"\d{1,6}[A-Za-z]?", line2) and not number:
            number = line2
        elif district or COMPLEMENT.search(line2) or re.search(r"\d", line2):
            complement_parts.append(line2)
        else:
            district = line2
    complete = bool(street and district)
    return {
        "street": street,
        "number": number or "S/N",
        "complement": ", ".join(p for p in complement_parts if p),
        "district": district,
        "complete": complete,
    }


def buyer_row(platform, row):
    """The buyer as DaVinci's copy of Bling shapes it (channel_recipients.from_bling_copy)."""
    address = split_address(platform, row)
    return {
        "name": _text(row.get("Nome do Destinatário")),
        "document": re.sub(r"\D", "", _text(row.get("ID da Taxa"))),
        "street": address["street"],
        "number": address["number"],
        "complement": address["complement"],
        "district": address["district"],
        "city": _text(row.get("Cidade")),
        "state": _text(row.get("Estado")),
        "postal_code": re.sub(r"\D", "", _text(row.get("CEP"))),
    }, address["complete"]


def _cancelled(row):
    text = " ".join(_key(row.get(c)) for c in ("Estado do Pedido", "Pós-venda/Cancelado/Devolvido"))
    return "cancel" in text


def group(platform, rows):
    """One order per platform number (one row per item); rows of other platforms are left out."""
    pattern, orders, others = NUMBERS[platform], {}, 0
    for row in rows:
        number = _text(row.get(ORDER))
        if _key(row.get("Plataformas")) != _key(PLATFORMS[platform]) or not pattern.fullmatch(
            number
        ):
            others += 1
            continue
        order = orders.setdefault(number, {"rows": []})
        order["rows"].append(row)
    return orders, others


def store_for(db, platform, name):
    key = _key(name)
    if key in STORES[platform]:
        return STORES[platform][key]
    bare = key.removeprefix(_key(PLATFORMS[platform])).strip()
    for store, data in db.execute(
        "SELECT store,data FROM store_policies WHERE channel=?", (platform,)
    ):
        own = _key(json.loads(data).get("name"))
        if own and own in {key, bare}:
            return store
    return None


def _order_read(number, rows):
    """The order in the strict shape of a native read (validate)."""
    first = rows[0]
    times = [m for c in TIMES for r in rows if (m := _moment(r.get(c)))]
    created = _moment(first.get("Hora do Pedido")) or (min(times) if times else None)
    if created is None:
        raise ERPError("upseller_order", f"Pedido {number} sem hora do pedido.")
    lines = []
    quantities = []
    for row in rows:
        sku = _text(row.get("SKU (Armazém)")) or None
        try:
            quantity = int(Decimal(str(row.get("Quantidade de Produtos") or 0)))
        except InvalidOperation:
            raise ERPError("upseller_order", f"Pedido {number} com quantidade ilegível.") from None
        quantities.append(max(quantity, 0))
        lines.append({"seller_sku": sku[:120] if sku else None, "quantity": max(quantity, 0)})
    products = _money(first.get("Valor Total de Produtos"))
    shared = [_money(r.get("Valor rateado por produto")) for r in rows]
    total_quantity = sum(quantities) or 1
    items = []
    for index, line in enumerate(lines):
        if any(shared):
            value = shared[index]
        elif len(lines) == 1:
            value = products
        else:
            value = (products * line["quantity"] / total_quantity).quantize(Decimal("0.01"))
        unit = (value / line["quantity"]).quantize(Decimal("0.01")) if line["quantity"] else value
        items.append(
            {
                "line_id": str(index + 1),
                "seller_sku": line["seller_sku"],
                "quantity": line["quantity"],
                "unit_price": _amount(unit),
                "discount": _amount(Decimal("0.00")),
            }
        )
    return {
        "id": number,
        "upseller_number": _text(first.get("Nº de Pedido"))[:40],
        "created_at": created.isoformat(),
        "updated_at": max([created, *times]).isoformat(),
        "status": "CANCELLED" if _cancelled(first) else "UPSELLER_COPY",
        "upseller_status": _text(first.get("Estado do Pedido"))[:60],
        "total": _amount(_money(first.get("Valor do Pedido"))),
        "products_total": _amount(products),
        "items": items,
    }


def _known(db, platform, store, number):
    """What the ERP has of this order: by source, with its newest observation."""
    try:
        rows = db.execute(
            "SELECT r.source,o.updated_at,o.data FROM native_order_observations o "
            "JOIN native_order_reads r ON r.digest=o.read_digest "
            "WHERE o.channel=? AND o.store=? AND o.external_id=? ORDER BY o.updated_at",
            (platform, store, number),
        ).fetchall()
    except Exception:  # noqa: BLE001 - tables appear with the first reading
        return {}
    known = {}
    for row in rows:
        known[row["source"]] = (row["updated_at"], json.loads(row["data"]))
    return known


def _same_order(a, b):
    def skus(order):
        return sorted(
            (str(i.get("seller_sku") or ""), int(i.get("quantity") or 0))
            for i in order.get("items") or []
        )

    fields = []
    if (str(a.get("status")).upper() == "CANCELLED") != (
        str(b.get("status")).upper() == "CANCELLED"
    ):
        fields.append("cancelamento")
    if skus(a) != skus(b):
        fields.append("itens")
    return fields


def _compare_buyer(stored, projection):
    fields = []
    for part, keys in (
        ("buyer", ("name", "document")),
        ("recipient", ("street", "number", "district", "city", "state", "postal_code")),
    ):
        for key in keys:
            mine = _key((projection.get(part) or {}).get(key))
            theirs = _key((stored.get(part) or {}).get(key))
            if key in {"number"}:
                mine, theirs = mine.replace(" ", ""), theirs.replace(" ", "")
            if mine != theirs:
                fields.append(f"{part}.{key}")
    return fields


def validate(read):
    """Strict shape of a read made from UpSeller's export (Temu orders)."""
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
                "limit_per_account",
                "accounts",
            }
            or read["version"] != 1
            or read["source"] != SOURCE
            or read["read_only"] != "on"
            or read["no_token_refresh"] is not True
            or read["external_business_writes"] is not False
            or read["limit_per_account"] != LIMIT
            or not isinstance(read["accounts"], list)
            or not 1 <= len(read["accounts"]) <= 4
        ):
            raise ValueError
        captured = datetime.fromisoformat(read["captured_at"])
        if captured.tzinfo is None or captured > datetime.now(UTC) + timedelta(minutes=5):
            raise ValueError
        for account in read["accounts"]:
            if (
                set(account)
                != {
                    "integration_id",
                    "platform",
                    "checked_at",
                    "date_from",
                    "date_to",
                    "state",
                    "orders",
                    "has_more",
                }
                or account["platform"] != "temu"
                or account["integration_id"] not in STORES["temu"].values()
                or account["state"] != "read_ok"
                or account["has_more"] is not False
                or not isinstance(account["orders"], list)
                or len(account["orders"]) > LIMIT
            ):
                raise ValueError
            seen = set()
            for order in account["orders"]:
                if set(order) != {
                    "id",
                    "upseller_number",
                    "created_at",
                    "updated_at",
                    "status",
                    "upseller_status",
                    "total",
                    "products_total",
                    "items",
                }:
                    raise ValueError
                if not NUMBERS["temu"].fullmatch(order["id"]) or order["id"] in seen:
                    raise ValueError
                seen.add(order["id"])
                if order["status"] not in {"UPSELLER_COPY", "CANCELLED"}:
                    raise ValueError
                created = datetime.fromisoformat(order["created_at"])
                updated = datetime.fromisoformat(order["updated_at"])
                if created.tzinfo is None or created > updated:
                    raise ValueError
                if not isinstance(order["items"], list) or not 0 < len(order["items"]) <= 200:
                    raise ValueError
                for item in order["items"]:
                    if set(item) != {"line_id", "seller_sku", "quantity", "unit_price", "discount"}:
                        raise ValueError
                    if type(item["quantity"]) is not int or not 0 <= item["quantity"] <= 1000000:
                        raise ValueError
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ERPError(
            "native_read_invalid", "Leitura nativa fora da projeção permitida."
        ) from None


def import_sheet(erp, content, platform, filtro="", now=None):
    from app.erp_local import bling_mirror, native_orders

    if platform not in PLATFORMS:
        raise ERPError("upseller_platform", "Plataforma do UpSeller: temu ou amazon.")
    head, rows = read_sheet(content)
    if platform == "temu":
        missing = sorted(REQUIRED_ORDERS - set(head))
        if missing:
            raise ERPError(
                "upseller_columns",
                "Faltam colunas na exportação do UpSeller: " + ", ".join(missing),
            )
    moment = (now or datetime.now(UTC)).replace(microsecond=0)
    orders, others = group(platform, rows)
    summary = {
        "plataforma": platform,
        "linhas": len(rows),
        "outras_plataformas": others,
        "pedidos": len(orders),
        "novos": 0,
        "atualizados": 0,
        "iguais": 0,
        "conferidos": 0,
        "divergencias": [],
        "cancelados": 0,
        "compradores": 0,
        "compradores_conferidos": 0,
        "compradores_divergentes": [],
        "sem_loja": [],
        "sem_endereco_completo": [],
        "filtro": _text(filtro)[:200],
        "importado_em": moment.isoformat(),
    }
    accounts = {}
    with erp.connection() as db:
        switched = bling_mirror.switched(db)
        for number, order in sorted(orders.items()):
            name = _text(order["rows"][0].get("Nome da Loja no UpSeller"))
            store = store_for(db, platform, name)
            if platform == "temu" and store not in STORES["temu"].values():
                store = None  # Temu only in the stores of the copy of Bling (one sale per order)
            if store is None:
                if name not in summary["sem_loja"]:
                    summary["sem_loja"].append(name)
                continue
            order["store"] = store
            if platform != "temu":
                continue
            read = _order_read(number, order["rows"])
            summary["cancelados"] += read["status"] == "CANCELLED"
            known = _known(db, platform, store, number)
            if "bling_copy_v1_read" in known and not switched:
                summary["conferidos"] += 1
                fields = _same_order(read, known["bling_copy_v1_read"][1])
                if fields:
                    summary["divergencias"].append({"pedido": number, "campos": fields})
                continue
            previous = known.get(SOURCE)
            if previous:
                old = {k: v for k, v in previous[1].items() if k != "updated_at"}
                if old == {k: v for k, v in read.items() if k != "updated_at"}:
                    summary["iguais"] += 1
                    continue
                last = datetime.fromisoformat(previous[0])
                stamp = datetime.fromisoformat(read["updated_at"])
                read["updated_at"] = max(stamp, last + timedelta(seconds=1)).isoformat()
                if datetime.fromisoformat(read["updated_at"]) > moment:
                    moment = datetime.fromisoformat(read["updated_at"])
                summary["atualizados"] += 1
            else:
                summary["novos"] += 1
            accounts.setdefault(store, []).append(read)
    if accounts:
        captured = moment.isoformat()
        native_orders.import_read(
            erp,
            {
                "version": 1,
                "source": SOURCE,
                "read_only": "on",
                "no_token_refresh": True,
                "external_business_writes": False,
                "captured_at": captured,
                "limit_per_account": LIMIT,
                "accounts": [
                    {
                        "integration_id": store,
                        "platform": "temu",
                        "checked_at": captured,
                        "date_from": min(o["created_at"] for o in found),
                        "date_to": captured,
                        "state": "read_ok",
                        "orders": found,
                        "has_more": False,
                    }
                    for store, found in sorted(accounts.items())
                ],
            },
        )
    for number, order in sorted(orders.items()):
        store = order.get("store")
        if not store:
            continue
        row, complete = buyer_row(platform, order["rows"][0])
        if not complete:
            summary["sem_endereco_completo"].append(number)
        projection = channel_recipients.from_bling_copy(row)
        with erp.connection() as db:
            stored = recipient_capture.for_order(db, platform, store, number)
        if stored and stored.get("ready"):
            summary["compradores_conferidos"] += 1
            fields = _compare_buyer(stored, projection)
            if fields:
                summary["compradores_divergentes"].append({"pedido": number, "campos": fields})
            continue
        if not complete:
            continue
        done = recipient_capture.capture(erp, platform, store, number, row, status="UPSELLER")
        summary["compradores"] += bool(done.get("ready"))
    summary["divergencias"] = summary["divergencias"][:100]
    summary["compradores_divergentes"] = summary["compradores_divergentes"][:100]
    summary["sem_endereco_completo"] = summary["sem_endereco_completo"][:100]
    with erp.connection() as db:
        db.executescript(SCHEMA)
        db.execute(
            "INSERT INTO upseller_imports(platform,imported_at,summary) VALUES (?,?,?)",
            (platform, summary["importado_em"], dumps(summary)),
        )
        db.execute(
            "DELETE FROM upseller_imports WHERE id NOT IN "
            "(SELECT id FROM upseller_imports ORDER BY id DESC LIMIT ?)",
            (KEEP_IMPORTS,),
        )
        db.commit()
    summary["digest"] = hashlib.sha256(dumps(summary).encode()).hexdigest()[:16]
    return summary


def latest(db):
    """The last import of each platform, for Integrações (no personal data is ever stored here)."""
    try:
        rows = db.execute(
            "SELECT platform,imported_at,summary FROM upseller_imports WHERE id IN "
            "(SELECT max(id) FROM upseller_imports GROUP BY platform)"
        ).fetchall()
    except Exception:  # noqa: BLE001 - table appears with the first import
        return {}
    return {r["platform"]: {"at": r["imported_at"], **json.loads(r["summary"])} for r in rows}
