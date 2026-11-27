"""Orders and buyers from UpSeller's export, so Temu, Shein, AliExpress and Amazon stop depending on
Bling.

UpSeller already has these stores connected, with the buyer whole (tested 27/11/2026 against
DaVinci's copy of Bling: name, CPF, CEP, house number and district equal on 3 Temu and 3 Amazon
orders). A robot on another PC (Mac mini de Jesse) exports from UpSeller every 10 minutes and sends
the spreadsheet here (docs/robo-upseller/PROMPT_ROBO_UPSELLER.md); a person can send the same file
from Integrações when the robot is down.

- Temu, Shein, AliExpress: an order the ERP does not have comes in as a read of its own source.
  Temu goes in the same stores the copy of Bling uses, so the two never make two sales of one
  order; while Bling still owns the stores, an order the copy already brought is only compared.
  Shein and AliExpress only come from here.
- Amazon: the orders come straight from Amazon; only the buyer comes from here.
- Everything UpSeller sends about each order, every column exactly as it came, is kept sealed
  (pii.seal, with the channel's retention: Amazon 30 days), owner's request of 27/11/2026.
- The buyer goes through recipient_capture, sealed like any other, always: when the address could
  not be split for sure it is kept marked "endereço a conferir" (not ready for an invoice). One
  already captured (from Bling) is compared, field by field, never overwritten here.

The spreadsheet is read in memory and never written to disk; nothing personal is logged or
returned, only order numbers, product codes and field names.
"""

import hashlib
import io
import json
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from app.erp_local import bling_copy_order_probe, channel_recipients, pii, recipient_capture
from app.erp_local.core import ERPError, dumps

SOURCE = "upseller_copy_v1_read"
LIMIT = 2000
MAX_BYTES = 10 * 1024 * 1024
BRT = ZoneInfo("America/Sao_Paulo")
PLATFORMS = {
    "temu": "Temu",
    "amazon": "Amazon",
    "shein": "Shein",
    "aliexpress": "AliExpress",
    "shopee": "Shopee",
    "ml": "Mercado Livre",
}
# The ones whose orders come in from here; Amazon's, Shopee's and ML's come from the stores
# themselves, save the stores the ERP does not read (UPSELLER_ONLY).
ORDER_PLATFORMS = {"temu", "shein", "aliexpress"}
NUMBERS = {
    "temu": bling_copy_order_probe.NUMBERS["temu"],
    "amazon": recipient_capture.AMAZON_ID,
    # GSH + 12 letters and digits (27/11/2026: KFA's orders); AliExpress: 16 digits.
    "shein": re.compile(r"[A-Za-z]{3}[A-Za-z0-9]{12}"),
    "aliexpress": re.compile(r"\d{16}"),
    "shopee": re.compile(r"\d{6}[A-Za-z0-9]{8}"),
    "ml": re.compile(r"\d{16}"),
}
# UpSeller's store name → the ERP store. Temu, Shein, AliExpress: by Bling store code (no
# integration in DaVinci; Temu shares the stores of the copy of Bling). Amazon: DaVinci's
# integrations. A name not here is matched by the store register's own name, else it is reported,
# never guessed. UpSeller calls the Shein store "KFA" and the AliExpress one "Shopee VIta".
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
        "nexus": "e5d6306b-d18f-47fe-af02-54cd26c5361d",  # UpSeller calls it "Nexus" (27/11)
        # No integration in DaVinci: the whole order comes from here (UPSELLER_ONLY).
        "amazon inova": "204438130",
        "amazon jlas": "205050265",
    },
    "shein": {
        "kfa": "204808793",
        "shein kfa": "204808793",
        "kia": "206080480",
        "shein kia": "206080480",
    },
    "aliexpress": {
        "shopee vita": "205295441",
        "aliexpress vita": "205295441",
        "vita": "205295441",
    },
    # Shopee and ML: only the stores the ERP does not read (UPSELLER_ONLY); the others are read
    # straight from Shopee and ML and are left alone here, never doubled.
    "shopee": {
        "shopee eron": "206356882",
        "eron": "206356882",
        "shopee vr": "206378665",
        "vr": "206378665",
        "shopee sal mg": "206081909",
        "sal mg": "206081909",
        "sal": "206081909",
    },
    "ml": {
        "ml eron 2": "206356887",
        "mercado livre eron 2": "206356887",
        "eron 2": "206356887",
        "eron2": "206356887",
    },
}
# Stores of read platforms that no integration in DaVinci reads (27/11/2026): their whole orders
# come from UpSeller, by Bling store code, like Temu.
UPSELLER_ONLY = {
    "amazon": {"204438130", "205050265"},
    "shopee": {"206356882", "206378665", "206081909"},
    "ml": {"206356887"},
}
STORE_NAMES = {
    "205925390": "Temu Barbosa",
    "206288651": "Temu JLAS",
    "206288703": "Temu Atv",
    "206288707": "Temu Eron",
    "204808793": "Shein KFA",
    "206080480": "Shein Kia",
    "205295441": "AliExpress Vita",
    "204438130": "Amazon Inova",
    "205050265": "Amazon Jlas",
    "206356882": "Shopee Eron",
    "206378665": "Shopee VR",
    "206081909": "Shopee Sal MG",
    "206356887": "ML Eron 2",
}


def order_stores(platform):
    """The stores whose whole orders come from UpSeller."""
    if platform in ORDER_PLATFORMS:
        return set(STORES[platform].values())
    return UPSELLER_ONLY.get(platform, set())


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
# Words that open a complement ("apto 12", "bl 3", "qd 4 lt 9", "casa", "fundos"). District words
# (jardim, vila, conjunto, residencial…) are not here: in Brazil they name the district.
COMPLEMENT_WORDS = {
    "ap",
    "apt",
    "apto",
    "apartamento",
    "casa",
    "cs",
    "bl",
    "bloco",
    "sobrado",
    "qd",
    "quadra",
    "lt",
    "lote",
    "torre",
    "sala",
    "sl",
    "loja",
    "fundos",
    "fds",
    "andar",
    "cond",
    "condominio",
    "ed",
    "edificio",
    "km",
    "box",
    "galpao",
}
NUMBER_WORDS = {"n", "no", "nº", "n°", "nro", "numero"}
VALUE = re.compile(r"^(\d{1,6}[a-z]?|[a-z]|º|°|\d{1,4}º|\d{1,4}°)$")
HOUSE = re.compile(r"^\d{1,6}[a-z]?$")
# Whatever UpSeller sends about an order that is not personal, kept on the order read too.
SHIPPING = {"method": "Método de Envio", "tracking": "Nº de Rastreio", "label": "Etiqueta"}
INVOICE = {
    "key": "Chave da Nota Fiscal",
    "number": "Número da NF-e",
    "series": "Número da Série",
}
VALUES = {
    "commission": "Comissão Total",
    "buyer_freight": "Frete do Comprador",
    "discounts": "Descontos e Cupons",
}
LISTING = {
    "name": "Nome do Anúncio",
    "id": "ID do Anúncio",
    "sku": "SKU",
    "variation": "Variação",
    "variant_id": "ID da Variante",
    "price": "Preço de Produto",
    "quantity": "Qtd. do Produto",
    "ncm": "NCM",
    "origin": "Origem",
    "unit": "Unidade",
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS upseller_imports (
 id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL, imported_at TEXT NOT NULL,
 summary TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS upseller_orders (
 platform TEXT NOT NULL, store TEXT NOT NULL, number TEXT NOT NULL, digest TEXT NOT NULL,
 sealed TEXT NOT NULL, state TEXT, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
 PRIMARY KEY (platform, store, number));
"""
KEEP_IMPORTS = 500
LISTS = (
    "divergencias",
    "compradores_divergentes",
    "sem_endereco_completo",
    "sem_dados_no_upseller",
    "sem_loja",
)


def _text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _key(value):
    plain = unicodedata.normalize("NFKD", _text(value)).encode("ascii", "ignore").decode()
    return plain.casefold()


def _word(token):
    return _key(token).strip(".,;:()-/'\"")


def _column(row, name):
    """A column by its name, with or without UpSeller's trailing asterisk ("NCM*")."""
    value = row.get(name)
    return row.get(name + "*") if value in (None, "") else value


def _cell(value):
    """A cell as UpSeller shows it."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y %H:%M:%S")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


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


def _complements(tokens):
    """Pull "apto 12", "bl 3", "qd 4 lt 9", "casa" out of tokens; what is left is the rest."""
    found, rest, index, joined = [], [], 0, False
    while index < len(tokens):
        if _word(tokens[index]) in COMPLEMENT_WORDS:
            group = [tokens[index]]
            index += 1
            while index < len(tokens) and VALUE.match(_word(tokens[index]) or "-"):
                group.append(tokens[index])
                index += 1
            # "bl 3 ap 4", "qd 9 lt 9": side by side they are one complement.
            if joined:
                found[-1] += " " + " ".join(group)
            else:
                found.append(" ".join(group))
            joined = True
            continue
        rest.append(tokens[index])
        joined = False
        index += 1
    return found, rest


def _clean_tokens(tokens):
    text = " ".join(tokens).strip(" ,;:-/")
    return re.sub(r"\s+([,;])", r"\1", text)


def split_address(platform, row):
    """Street, number, complement and district apart, as the invoice needs them.

    Temu: the district comes in its column; the number column is masked, the number is in
    "Endereço 1". Amazon: number and district columns come empty (608 orders of 27/11/2026); the
    number is at the end of "Endereço 1" ("rua X 120", sometimes "rua X, 120" or "numero 120") or
    alone in "Endereço 2", and "Endereço 2" carries the complement and the district together
    ("apto 12 centro", "casa vila nova", "bl 3 ap 4 jardim X"). "certain" is false when no district
    could be told apart: the buyer is kept, marked to be checked.
    """
    line1, line2 = _text(row.get("Endereço 1")), _text(row.get("Endereço 2"))
    given = _text(row.get("Número"))
    district = _text(row.get("Bairro"))
    # UpSeller joins Amazon's complement and district with two spaces ("apto 12  Centro"): the
    # last part is the district (5 of 5 equal to Bling on 27/11/2026).
    parts = [p for p in re.split(r"\s{2,}", str(row.get("Endereço 2") or "").strip()) if p.strip()]
    if len(parts) > 1 and not district:
        district, line2 = _text(parts[-1]), _text(" ".join(parts[:-1]))
    number, complements, tails = "", [], []
    if given and "*" not in given and re.fullmatch(r"\d{1,6}[A-Za-z]?", given):
        number = given
    elif _key(given) in {"s/n", "sn"}:
        number = "S/N"
    # "bairro: X" anywhere names the district.
    for line in (line1, line2):
        named = re.search(r"\bbairro\s*:?\s*([^,;]+)", line, re.IGNORECASE)
        if named and not district:
            district = named.group(1).strip(" -")
    line1 = re.sub(r",?\s*\bbairro\s*:?\s*[^,;]+", "", line1, flags=re.IGNORECASE).strip()
    line2 = re.sub(r",?\s*\bbairro\s*:?\s*[^,;]+", "", line2, flags=re.IGNORECASE).strip()
    # "Rua X - Vila Nova": a last part with no digit and no complement word is the district.
    if not district and " - " in line1:
        head_part, _, tail_part = line1.rpartition(" - ")
        words = tail_part.split()
        if (
            words
            and not re.search(r"\d", tail_part)
            and not any(_word(w) in COMPLEMENT_WORDS for w in words)
        ):
            district, line1 = tail_part.strip(), head_part.strip()
    # "s/n", "sn", "x / n" say there is no number.
    no_number = re.search(r"(^|\s)(s\s*/\s*n|sn|/\s*n)(\s|,|$)", line1, re.IGNORECASE)
    if no_number:
        line1 = (line1[: no_number.start()] + " " + line1[no_number.end() :]).strip(" ,")
        number = number or "S/N"
    street = line1
    comma = re.search(r",\s*(?:(?:n[º°o.]?|numero|número|nro)\s*)?(\d{1,6}[A-Za-z]?)\b(.*)$", line1)
    marked = re.search(r"\b(?:n[º°o.]|numero|número|nro)\s*(\d{1,6}[A-Za-z]?)\b(.*)$", line1, re.I)
    if comma:
        number = number or comma.group(1)
        street = line1[: comma.start()]
        tails.extend(p for p in comma.group(2).split(",") if p.strip())
    elif marked:
        number = number or marked.group(1)
        street = line1[: marked.start()]
        if marked.group(2).strip():
            tails.append(marked.group(2))
    else:
        tokens = line1.split()
        chosen = None
        for index, token in enumerate(tokens):
            if (
                index
                and HOUSE.match(_word(token))
                and _word(tokens[index - 1]) not in COMPLEMENT_WORDS
                and any(re.search(r"[A-Za-z]", t) for t in tokens[:index])
            ):
                chosen = index
        if chosen is not None:
            number = number or tokens[chosen]
            street = " ".join(tokens[:chosen])
            if tokens[chosen + 1 :]:
                tails.append(" ".join(tokens[chosen + 1 :]))
        else:
            found, rest = _complements(tokens)
            complements.extend(found)
            street = " ".join(rest)
    for tail in tails:
        found, rest = _complements(tail.split())
        complements.extend(found)
        if rest and not district:
            district = _clean_tokens(rest)
        elif rest:
            complements.append(_clean_tokens(rest))
    if line2:
        tokens = line2.split()
        if len(tokens) == 1 and HOUSE.match(_word(tokens[0])) and not number:
            number = tokens[0]
        else:
            found, rest = _complements(tokens)
            complements.extend(found)
            # A number opening what is left is an apartment or the house, never the district.
            while rest and HOUSE.match(_word(rest[0])):
                if number:
                    complements.append(rest.pop(0))
                else:
                    number = rest.pop(0)
            rest_text = _clean_tokens(rest)
            if rest_text and not district:
                district = rest_text
            elif rest_text:
                complements.append(rest_text)
    street = street.strip(" ,;-")
    return {
        "street": street,
        "number": number or "S/N",
        "complement": ", ".join(c.strip(" ,;-") for c in complements if c.strip(" ,;-")),
        "district": district.strip(" ,;-"),
        "certain": bool(street and district.strip(" ,;-")),
    }


def buyer_row(platform, row):
    """The buyer as DaVinci's copy of Bling shapes it (channel_recipients.from_bling_copy)."""
    address = split_address(platform, row)
    data = {
        "name": _text(row.get("Nome do Destinatário")),
        "document": re.sub(r"\D", "", _text(row.get("ID da Taxa"))),
        "street": address["street"],
        "number": address["number"],
        "complement": address["complement"],
        "district": address["district"],
        "city": _text(row.get("Cidade")),
        "state": _text(row.get("Estado")),
        "postal_code": re.sub(r"\D", "", _text(row.get("CEP"))),
    }
    if not address["certain"]:
        data["address_review"] = True
    return data, address


def _has_buyer(row):
    return any(
        _text(row.get(c))
        for c in ("Nome do Destinatário", "ID da Taxa", "Endereço 1", "Endereço 2", "CEP")
    )


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
    # UpSeller writes three Shopee stores "Shoppe" (Barbosa, Vitor Mei, Atv).
    bare = re.sub(r"^shoppe\b", "shopee", key)
    bare = bare.removeprefix(_key(PLATFORMS[platform])).strip()
    for store, data in db.execute(
        "SELECT store,data FROM store_policies WHERE channel=?", (platform,)
    ):
        own = _key(json.loads(data).get("name"))
        if own and own in {key, bare}:
            return store
    return None


def _ensure_store(db, platform, store, now):
    """Stores read only from here (Shein, AliExpress; Temu if the copy has not adopted it yet)
    enter the store register in review, like a store DaVinci connects (adopt_new_stores)."""
    db.execute(
        "INSERT INTO store_policies VALUES (?,?,'identity_review',?,?) "
        "ON CONFLICT(channel,store) DO UPDATE SET status='identity_review',updated_at=? "
        "WHERE store_policies.status='inactive'",
        (
            platform,
            store,
            dumps({"adopted_from_upseller": True, "name": STORE_NAMES.get(store, store)}),
            now,
            now,
        ),
    )


def _strings(row, columns):
    return {key: _cell(_column(row, column))[:500] for key, column in columns.items()}


def _order_read(number, rows):
    """The order in the strict shape of a native read (validate)."""
    first = rows[0]
    times = [m for c in TIMES for r in rows if (m := _moment(r.get(c)))]
    created = _moment(first.get("Hora do Pedido")) or (min(times) if times else None)
    if created is None:
        raise ERPError("upseller_order", f"Pedido {number} sem hora do pedido.")
    lines = []
    for row in rows:
        # The listing's SKU first: UpSeller's warehouse SKU came as "e3" on every Temu order of
        # 27/11/2026, while the listing's (a076, a015) is the product, as Bling has it.
        sku = _text(_column(row, "SKU")) or _text(row.get("SKU (Armazém)")) or None
        raw_quantity = row.get("Quantidade de Produtos")
        if raw_quantity in (None, ""):
            raw_quantity = _column(row, "Qtd. do Produto")
        try:
            quantity = max(int(Decimal(str(raw_quantity or 0))), 0)
        except InvalidOperation:
            raise ERPError("upseller_order", f"Pedido {number} com quantidade ilegível.") from None
        price = _column(row, "Preço de Produto")
        lines.append(
            {
                "seller_sku": sku[:120] if sku else None,
                "quantity": quantity,
                "price": _money(price) if price not in (None, "") else None,
                "listing": _strings(row, LISTING),
            }
        )
    products = _money(first.get("Valor Total de Produtos"))
    shared = [_money(r.get("Valor rateado por produto")) for r in rows]
    total_quantity = sum(line["quantity"] for line in lines) or 1
    items = []
    for index, line in enumerate(lines):
        if line["price"] is not None:
            unit = line["price"]
        else:
            if any(shared):
                value = shared[index]
            elif len(lines) == 1:
                value = products
            else:
                value = (products * line["quantity"] / total_quantity).quantize(Decimal("0.01"))
            unit = (
                (value / line["quantity"]).quantize(Decimal("0.01")) if line["quantity"] else value
            )
        items.append(
            {
                "line_id": str(index + 1),
                "seller_sku": line["seller_sku"],
                "quantity": line["quantity"],
                "unit_price": _amount(unit),
                "discount": _amount(Decimal("0.00")),
                "listing": line["listing"],
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
        "values": {k: f"{_money(first.get(c)):.2f}" for k, c in VALUES.items()},
        "shipping": _strings(first, SHIPPING),
        "invoice": _strings(first, INVOICE),
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


def _skus(order):
    from app.erp_local.bling_mirror import family

    out = {}
    for item in order.get("items") or []:
        code = family(str(item.get("seller_sku") or "").strip()) or "?"
        out[code] = out.get(code, 0) + int(item.get("quantity") or 0)
    return sorted(out.items())


def _same_order(mine, theirs):
    """Fields that differ from the copy of Bling, with both item lists (product codes only)."""
    fields = []
    cancelled = str(mine.get("status")).upper() == "CANCELLED"
    if cancelled != (str(theirs.get("status")).upper() == "CANCELLED"):
        fields.append("cancelamento")
    if _skus(mine) != _skus(theirs):
        fields.append("itens")
    return fields


def _compare_buyer(stored, projection):
    fields = []
    for part, keys in (
        ("buyer", ("name", "document")),
        ("recipient", ("street", "number", "district", "city", "state", "postal_code")),
    ):
        for key in keys:
            mine = _key((projection.get(part) or {}).get(key)).replace(" ", "")
            theirs = _key((stored.get(part) or {}).get(key)).replace(" ", "")
            if mine != theirs:
                fields.append(f"{part}.{key}")
    return fields


def _keep_raw(erp, platform, store, number, head, rows, moment):
    """Every column UpSeller sent about this order, as it came, sealed. True when new or changed."""
    value = {"columns": head, "rows": [[_cell(r.get(c)) for c in head] for r in rows]}
    digest = hashlib.sha256(dumps(value).encode()).hexdigest()
    state = _text(rows[0].get("Estado do Pedido"))[:60]
    with erp.connection() as db:
        db.executescript(SCHEMA)
        old = db.execute(
            "SELECT digest FROM upseller_orders WHERE platform=? AND store=? AND number=?",
            (platform, store, number),
        ).fetchone()
        if old and old[0] == digest:
            db.execute(
                "UPDATE upseller_orders SET last_seen=? WHERE platform=? AND store=? AND number=?",
                (moment, platform, store, number),
            )
            db.commit()
            return False
        sealed = pii.seal(db, f"upseller:{platform}:{store}:{number}", platform, value)
        db.execute(
            "INSERT INTO upseller_orders VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(platform,store,number) DO UPDATE SET digest=excluded.digest,"
            "sealed=excluded.sealed,state=excluded.state,last_seen=excluded.last_seen",
            (platform, store, number, digest, dumps(sealed), state, moment, moment),
        )
        db.commit()
    return True


def raw_order(db, platform, store, number):
    """The order as UpSeller sent it ({columns, rows}), opened; None when absent or purged."""
    try:
        row = db.execute(
            "SELECT sealed FROM upseller_orders WHERE platform=? AND store=? AND number=?",
            (platform, store, number),
        ).fetchone()
    except Exception:  # noqa: BLE001 - table appears with the first import
        return None
    if not row:
        return None
    try:
        return pii.open_value(db, json.loads(row[0]))
    except ERPError:
        return None


def validate(read):
    """Strict shape of a read made from UpSeller's export (Temu, Shein, AliExpress orders)."""

    def strings(value, keys):
        if not isinstance(value, dict) or set(value) != set(keys):
            raise ValueError
        for item in value.values():
            if not isinstance(item, str) or len(item) > 500:
                raise ValueError

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
            or not 1 <= len(read["accounts"]) <= 12
        ):
            raise ValueError
        captured = datetime.fromisoformat(read["captured_at"])
        if captured.tzinfo is None or captured > datetime.now(UTC) + timedelta(minutes=5):
            raise ValueError
        for account in read["accounts"]:
            platform = account.get("platform")
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
                or platform not in PLATFORMS
                or account["integration_id"] not in order_stores(platform)
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
                    "values",
                    "shipping",
                    "invoice",
                    "items",
                }:
                    raise ValueError
                if not NUMBERS[platform].fullmatch(order["id"]) or order["id"] in seen:
                    raise ValueError
                seen.add(order["id"])
                if order["status"] not in {"UPSELLER_COPY", "CANCELLED"}:
                    raise ValueError
                created = datetime.fromisoformat(order["created_at"])
                updated = datetime.fromisoformat(order["updated_at"])
                if created.tzinfo is None or created > updated:
                    raise ValueError
                strings(order["values"], VALUES)
                strings(order["shipping"], SHIPPING)
                strings(order["invoice"], INVOICE)
                if not isinstance(order["items"], list) or not 0 < len(order["items"]) <= 200:
                    raise ValueError
                for item in order["items"]:
                    if set(item) != {
                        "line_id",
                        "seller_sku",
                        "quantity",
                        "unit_price",
                        "discount",
                        "listing",
                    }:
                        raise ValueError
                    if type(item["quantity"]) is not int or not 0 <= item["quantity"] <= 1000000:
                        raise ValueError
                    strings(item["listing"], LISTING)
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ERPError(
            "native_read_invalid", "Leitura nativa fora da projeção permitida."
        ) from None


def import_sheet(erp, content, platform, filtro="", now=None):
    from app.erp_local import bling_mirror, native_orders

    if platform not in PLATFORMS:
        raise ERPError(
            "upseller_platform", "Plataforma do UpSeller: temu, amazon, shein ou aliexpress."
        )
    head, rows = read_sheet(content)
    if platform in ORDER_PLATFORMS:
        missing = sorted(REQUIRED_ORDERS - set(head))
        if missing:
            raise ERPError(
                "upseller_columns",
                "Faltam colunas na exportação do UpSeller: " + ", ".join(missing),
            )
    moment = (now or datetime.now(UTC)).replace(microsecond=0)
    stamp = moment.isoformat()
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
        "dados_gravados": 0,
        "compradores": 0,
        "compradores_conferidos": 0,
        "compradores_divergentes": [],
        "sem_loja": [],
        "sem_endereco_completo": [],
        "sem_dados_no_upseller": [],
        "lidos_direto": 0,
        "filtro": _text(filtro)[:200],
        "importado_em": stamp,
    }
    accounts = {}
    with erp.connection() as db:
        switched = bling_mirror.switched(db)
        for number, order in sorted(orders.items()):
            name = _text(order["rows"][0].get("Nome da Loja no UpSeller"))
            store = store_for(db, platform, name)
            if platform in ORDER_PLATFORMS and store not in STORES[platform].values():
                store = None  # only the known stores: one sale per order, never a guessed store
            if platform in {"shopee", "ml"} and store and store not in UPSELLER_ONLY[platform]:
                # Read straight from Shopee or ML with their own buyer: nothing to do here.
                summary["lidos_direto"] += 1
                continue
            if store is None:
                if name not in summary["sem_loja"]:
                    summary["sem_loja"].append(name)
                continue
            order["store"] = store
            if store not in order_stores(platform):
                continue
            missing = sorted(REQUIRED_ORDERS - set(head))
            if missing:
                raise ERPError(
                    "upseller_columns",
                    "Faltam colunas na exportação do UpSeller: " + ", ".join(missing),
                )
            _ensure_store(db, platform, store, stamp)
            read = _order_read(number, order["rows"])
            summary["cancelados"] += read["status"] == "CANCELLED"
            known = _known(db, platform, store, number)
            if "bling_copy_v1_read" in known and not switched:
                summary["conferidos"] += 1
                theirs = known["bling_copy_v1_read"][1]
                fields = _same_order(read, theirs)
                if fields:
                    summary["divergencias"].append(
                        {
                            "pedido": number,
                            "campos": fields,
                            "upseller": _skus(read),
                            "bling": _skus(theirs),
                        }
                    )
                continue
            previous = known.get(SOURCE)
            if previous:
                old = {k: v for k, v in previous[1].items() if k != "updated_at"}
                if old == {k: v for k, v in read.items() if k != "updated_at"}:
                    summary["iguais"] += 1
                    continue
                last = datetime.fromisoformat(previous[0])
                changed = datetime.fromisoformat(read["updated_at"])
                read["updated_at"] = max(changed, last + timedelta(seconds=1)).isoformat()
                if datetime.fromisoformat(read["updated_at"]) > moment:
                    moment = datetime.fromisoformat(read["updated_at"])
                summary["atualizados"] += 1
            else:
                summary["novos"] += 1
            accounts.setdefault((platform, store), []).append(read)
        db.commit()
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
                        "platform": channel,
                        "checked_at": captured,
                        "date_from": min(o["created_at"] for o in found),
                        "date_to": captured,
                        "state": "read_ok",
                        "orders": found,
                        "has_more": False,
                    }
                    for (channel, store), found in sorted(accounts.items())
                ],
            },
        )
    for number, order in sorted(orders.items()):
        store = order.get("store")
        if not store:
            continue
        try:
            summary["dados_gravados"] += _keep_raw(
                erp, platform, store, number, head, order["rows"], stamp
            )
        except ERPError as error:
            if error.code not in {"pii_key_missing", "pii_purged"}:
                raise
        first = order["rows"][0]
        if not _has_buyer(first):
            # Amazon keeps the buyer of unpaid and cancelled sales from everyone (UpSeller too).
            summary["sem_dados_no_upseller"].append(number)
            continue
        row, address = buyer_row(platform, first)
        if not address["certain"]:
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
        done = recipient_capture.capture(erp, platform, store, number, row, status="UPSELLER")
        summary["compradores"] += not done.get("kept") and not done.get("state")
    for name in LISTS:
        summary[name + "_total"] = len(summary[name])
    return _record(erp, platform, summary)


def empty_round(erp, platform, filtro="", now=None):
    """The robot looked and the platform had no order in its window (AliExpress, ML Eron 2 and
    the Shopee stores sell rarely): recorded like an import with no order, so Integrações tells a
    quiet platform from a robot that stopped (27/11/2026)."""
    if platform not in PLATFORMS:
        raise ERPError(
            "upseller_platform",
            "Plataforma do UpSeller: temu, amazon, shein, aliexpress, shopee ou ml.",
        )
    summary = {
        "plataforma": platform,
        "rodada_vazia": True,
        "linhas": 0,
        "outras_plataformas": 0,
        "pedidos": 0,
        "novos": 0,
        "atualizados": 0,
        "iguais": 0,
        "conferidos": 0,
        "cancelados": 0,
        "dados_gravados": 0,
        "compradores": 0,
        "compradores_conferidos": 0,
        "lidos_direto": 0,
        "filtro": _text(filtro)[:200],
        "importado_em": (now or datetime.now(UTC)).replace(microsecond=0).isoformat(),
    }
    for name in LISTS:
        summary[name] = []
        summary[name + "_total"] = 0
    return _record(erp, platform, summary)


def _record(erp, platform, summary):
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
    out = {}
    for r in rows:
        summary = json.loads(r["summary"])
        # Integrações shows counts and a few numbers; the robot gets the full lists in the reply.
        for name in LISTS:
            summary[name] = summary.get(name, [])[:20]
        out[r["platform"]] = {"at": r["imported_at"], **summary}
    return out
