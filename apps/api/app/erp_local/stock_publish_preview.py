"""Preview, read only: what each store listing would show if DaVinci took its stock from the ERP.

Today DaVinci itself sends the number to the listings (sync_orchestrator): the product's available
balance read from Bling (saldoVirtualTotal; a kit has what its scarcest part allows), or the
family total when the family sum is on (estoque_familia.saldo_publicavel). This repeats that same
number with the ERP's balances and compares it listing by listing. Nothing is written at the
stores, in DaVinci or in Bling. Listings DaVinci itself skips (dead, archived account, vacation
mode) are only counted.
"""

import json
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation

from app.erp_local import deposits, product_details, stock_families
from app.erp_local.core import ERPError

LINKS_SQL = (
    "SELECT coalesce(json_agg(t),'[]') FROM (SELECT l.platform::text AS channel,"
    " coalesce(i.name,'') AS store, l.external_id AS listing,"
    " coalesce(l.variation_id,'') AS variation, l.stock AS listing_stock, p.sku,"
    " p.stock AS product_stock, coalesce(p.situacao,'A') AS situation,"
    " p.bling_product_id::text AS bling_id, (l.morto_desde IS NOT NULL) AS dead,"
    " (i.archived_at IS NOT NULL) AS archived, coalesce(i.vacation_mode,false) AS vacation"
    " FROM davinci.product_links l JOIN davinci.products p ON p.id=l.product_id"
    " LEFT JOIN davinci.integrations i ON i.id=l.integration_id"
    " WHERE l.platform::text<>'bling') t"
)
PRODUCTS_SQL = (
    "SELECT coalesce(json_agg(t),'[]') FROM (SELECT lower(sku) AS sku, stock"
    " FROM davinci.products WHERE coalesce(situacao,'A')='A' AND sku IS NOT NULL) t"
)
REASONS = {
    "igual": "Igual ao que o DaVinci manda hoje",
    "fora_do_erp": "Produto não está no ERP",
    "fora_do_erp_inativo": "Produto inativo ou excluído no Bling, fora do ERP",
    "saldo_desconhecido": "ERP ainda não sabe o saldo",
    "inativo": "Produto inativo no ERP: anúncio vai a 0",
    "kit": "Kit: saldo calculado pelas peças diferente",
    "familia": "Soma da família diferente",
    "simples": "Saldo do produto diferente",
}


# The family sum as DaVinci's workers ran it on 10/11/2026 (on, every line, floor 5). The ERP
# worker does not carry DaVinci's ESTOQUE_FAMILIA_* keys; when they are absent these are used and
# the report says so, so a change in DaVinci shows up as a family difference, not silence.
FAMILY_10_11 = {"active": True, "prefixes": (), "minimum": 5, "source": "DaVinci em 10/11"}


def family_settings():
    """The family-sum switches DaVinci runs with."""
    import os

    if not any(name.upper().startswith("ESTOQUE_FAMILIA_") for name in os.environ):
        return dict(FAMILY_10_11)
    try:
        from app.config import get_settings

        s = get_settings()
        return {
            "active": bool(getattr(s, "estoque_familia_ativo", False)),
            "prefixes": tuple(
                p.strip().lower()
                for p in (getattr(s, "estoque_familia_prefixos", "") or "").split(",")
                if p.strip()
            ),
            "minimum": int(getattr(s, "estoque_familia_minimo", 0) or 0),
            "source": "configuração",
        }
    except Exception:  # noqa: BLE001 - outside DaVinci the sum is simply off
        return {"active": False, "prefixes": (), "minimum": 0, "source": "sem configuração"}


def _int(value):
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return 0


def _family_on(sku, family):
    base = stock_families.key(sku)
    if not family["active"] or base is None:
        return None
    if family["prefixes"] and not base.split("+")[0].startswith(family["prefixes"]):
        return None
    return base


def siblings(base):
    return [
        "+".join(f"{p}.{lot}" for p in base.split("+")) for lot in sorted(stock_families.SALE_LOTS)
    ]


def published(sku, own, active, stock_of, family):
    """estoque_familia.saldo_publicavel with any source of balances."""
    own = max(0, own)
    base = _family_on(sku, family) if active else None
    if base is None:
        return own
    total = sum(max(0, stock_of(name)) for name in siblings(base))
    return own if total < family["minimum"] else max(total, own)


def erp_available(db):
    """Available per SKU as the ERP answers Bling's saldoVirtualTotal; None while unknown."""
    counted = [d["id"] for d in deposits.snapshot(db) if not d["exclude_from_total"]]
    balance = defaultdict(lambda: (Decimal(0), Decimal(0)))
    for r in db.execute("SELECT sku,depot,physical,reserved FROM balances"):
        balance[(r["sku"], r["depot"])] = (Decimal(r["physical"]), Decimal(r["reserved"]))
    known = {}
    for r in db.execute("SELECT sku,data FROM catalog_origin"):
        known[r["sku"]] = json.loads(r["data"]).get("stock_state", "known") == "known"
    products = {
        r["sku"]: (r["kind"], json.loads(r["components"]))
        for r in db.execute("SELECT sku,kind,components FROM products")
    }
    result = {}
    for sku, (kind, components) in products.items():
        if kind == "service":
            continue
        if kind == "kit":
            if not components or not all(known.get(p["sku"], True) for p in components):
                result[sku] = None
                continue
            total = Decimal(0)
            for depot in counted:
                total += min(
                    (balance[(p["sku"], depot)][0] - balance[(p["sku"], depot)][1])
                    // Decimal(p["quantity"])
                    for p in components
                )
            result[sku] = total
        elif not known.get(sku, True):
            result[sku] = None
        else:
            result[sku] = sum(
                (balance[(sku, depot)][0] - balance[(sku, depot)][1] for depot in counted),
                Decimal(0),
            )
    return result, {sku: kind for sku, (kind, _) in products.items()}


def compare(db, links, davinci_products, family):
    available, kinds = erp_available(db)
    by_bling = {}
    for r in db.execute("SELECT sku,data FROM catalog_origin"):
        source = json.loads(r["data"]).get("bling_product_id")
        if source:
            by_bling[str(source)] = r["sku"]
    by_lower = {sku.lower(): sku for sku in kinds}
    active = {sku: product_details.details(db, sku)["status"] == "active" for sku in kinds}
    davinci_stock = defaultdict(int)
    for p in davinci_products:
        davinci_stock[p["sku"]] += max(0, _int(p.get("stock")))

    def erp_stock(name):
        sku = by_lower.get(name)
        if not sku or not active.get(sku):
            return 0
        return _int(available.get(sku) or 0)

    summary, skipped = Counter(), Counter()
    stores, rows = defaultdict(Counter), []
    for link in links:
        if link.get("dead") or link.get("archived") or link.get("vacation"):
            skipped[
                "morto" if link.get("dead") else "arquivada" if link.get("archived") else "ferias"
            ] += 1
            continue
        sku = link.get("sku") or ""
        today = published(
            sku,
            _int(link.get("product_stock")),
            link.get("situation") == "A",
            lambda name: davinci_stock.get(name, 0),
            family,
        )
        erp_sku = by_bling.get(str(link.get("bling_id"))) or by_lower.get(sku.lower())
        if not erp_sku:
            reason, erp = (
                ("fora_do_erp" if link.get("situation") == "A" else "fora_do_erp_inativo"),
                None,
            )
        elif not active[erp_sku]:
            # Inactive or excluded here: the listing gets 0 (channels.preview_quantity).
            erp = 0
            reason = "igual" if today == 0 else "inativo"
        elif available.get(erp_sku) is None:
            reason, erp = "saldo_desconhecido", None
        else:
            own = _int(available[erp_sku])
            erp = published(erp_sku, own, True, erp_stock, family)
            if erp == today:
                reason = "igual"
            elif max(0, own) != max(0, _int(link.get("product_stock"))):
                reason = "kit" if kinds[erp_sku] == "kit" else "simples"
            else:
                reason = "familia"
        summary[reason] += 1
        store_key = (link.get("channel") or "", link.get("store") or "")
        stores[store_key]["anuncios"] += 1
        stores[store_key]["iguais" if reason == "igual" else "diferentes"] += 1
        if reason != "igual":
            rows.append(
                {
                    "canal": store_key[0],
                    "loja": store_key[1],
                    "anuncio": link.get("listing") or "",
                    "variacao": link.get("variation") or "",
                    "sku": sku,
                    "sku_erp": erp_sku or "",
                    "davinci_manda": today,
                    "erp_mandaria": erp,
                    "ultimo_enviado": link.get("listing_stock"),
                    "motivo": REASONS[reason],
                }
            )
    considered = sum(summary.values())
    return {
        "anuncios": considered,
        "iguais": summary["igual"],
        "diferentes": considered - summary["igual"],
        "motivos": {REASONS[k]: v for k, v in summary.most_common()},
        "fora_da_conta": dict(skipped),
        "soma_da_familia": {
            "ligada": family["active"],
            "prefixos": list(family["prefixes"]),
            "minimo": family["minimum"],
            "origem": family.get("source", "informada"),
        },
        "lojas": [
            {"canal": ch, "loja": st, **dict(counts)} for (ch, st), counts in sorted(stores.items())
        ],
        "diferencas": rows,
    }


def run(erp, reader=None, family=None):
    """One pass over every listing DaVinci knows; the ERP is only read."""
    if reader is None:
        from app.erp_local.bling_mirror import read_local as reader
    links, products = reader(LINKS_SQL), reader(PRODUCTS_SQL)
    if not isinstance(links, list) or not isinstance(products, list):
        raise ERPError("source_unavailable", "Leitura dos anúncios veio vazia.", 502)
    with erp.connection() as db:
        return compare(db, links, products, family or family_settings())
