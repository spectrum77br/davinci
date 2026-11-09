"""Who buys and who receives, from each store's own reply. Runs where the credentials live.

Field names come from the 06/11 probe. A masked or missing value never becomes data: the order
stops with the reason, so an invoice is never issued from a partial buyer. The correspondence of
each field still has to be proven in the parallel run against Bling.
"""

import re

from app.erp_local.core import ERPError
from app.erp_local.cutover import _fold, city_code

UF = re.compile(r"[A-Z]{2}$")
STREET_NUMBER = re.compile(r"(\d+[A-Za-z]?|s\.?/?n\.?)$", re.IGNORECASE)
UF_NAMES = {
    _fold(name): code
    for code, name in {
        "AC": "Acre",
        "AL": "Alagoas",
        "AM": "Amazonas",
        "AP": "Amapá",
        "BA": "Bahia",
        "CE": "Ceará",
        "DF": "Distrito Federal",
        "ES": "Espírito Santo",
        "GO": "Goiás",
        "MA": "Maranhão",
        "MG": "Minas Gerais",
        "MS": "Mato Grosso do Sul",
        "MT": "Mato Grosso",
        "PA": "Pará",
        "PB": "Paraíba",
        "PE": "Pernambuco",
        "PI": "Piauí",
        "PR": "Paraná",
        "RJ": "Rio de Janeiro",
        "RN": "Rio Grande do Norte",
        "RO": "Rondônia",
        "RR": "Roraima",
        "RS": "Rio Grande do Sul",
        "SC": "Santa Catarina",
        "SE": "Sergipe",
        "SP": "São Paulo",
        "TO": "Tocantins",
    }.items()
}


def _clean(value):
    text = str(value or "").strip()
    return "" if "*" in text else text


def _digits(value):
    return re.sub(r"\D", "", _clean(value))


def _finish(buyer, delivery):
    reasons = []
    if len(buyer["document"]) not in {11, 14}:
        reasons.append("documento")
    if not buyer["name"]:
        reasons.append("nome")
    for field, label in [
        ("street", "logradouro"),
        ("city", "município"),
        ("state", "UF"),
        ("postal_code", "CEP"),
    ]:
        if not delivery[field]:
            reasons.append(label)
    if delivery["state"] and not UF.fullmatch(delivery["state"]):
        reasons.append("UF")
    delivery["ibge_code"] = city_code(delivery["city"], delivery["state"])
    if not delivery["ibge_code"]:
        reasons.append("município sem código oficial")
    return {"buyer": buyer, "recipient": delivery, "missing": reasons, "ready": not reasons}


def from_ml(billing, shipment):
    info = (billing or {}).get("billing_info") or {}
    extra = {row.get("type"): row.get("value") for row in info.get("additional_info") or []}
    company = _clean(extra.get("BUSINESS_NAME"))
    person = " ".join(
        p for p in [_clean(extra.get("FIRST_NAME")), _clean(extra.get("LAST_NAME"))] if p
    )
    destination = (shipment or {}).get("destination") or {}
    address = destination.get("shipping_address") or {}
    state = _clean((address.get("state") or {}).get("id")).removeprefix("BR-")
    return _finish(
        {"name": company or person, "document": _digits(info.get("doc_number"))},
        {
            "name": _clean(destination.get("receiver_name")),
            "street": _clean(address.get("street_name")),
            "number": _clean(address.get("street_number")) or "S/N",
            "complement": _clean(address.get("comment")),
            "district": _clean((address.get("neighborhood") or {}).get("name")),
            "city": _clean((address.get("city") or {}).get("name")),
            "state": state,
            "postal_code": _digits(address.get("zip_code")),
            "phone": _digits(destination.get("receiver_phone")),
        },
    )


def from_magalu(order):
    customer = order.get("customer") or {}
    deliveries = order.get("deliveries") or [{}]
    recipient = (deliveries[0].get("shipping") or {}).get("recipient") or {}
    address = recipient.get("address") or {}
    return _finish(
        {
            "name": _clean(customer.get("name")),
            "document": _digits(customer.get("document_number")),
        },
        {
            "name": _clean(recipient.get("name")),
            "street": _clean(address.get("street")),
            "number": _clean(address.get("number")) or "S/N",
            "complement": _clean(address.get("complement")),
            "district": _clean(address.get("district")),
            "city": _clean(address.get("city")),
            "state": _clean(address.get("state")),
            "postal_code": _digits(address.get("zipcode")),
            "phone": "",
        },
    )


def from_tiktok(order):
    """The level names in district_info are the store's; a level not found stays empty."""
    address = order.get("recipient_address") or {}
    levels = {
        str(row.get("address_level_name") or "").casefold(): _clean(row.get("address_name"))
        for row in address.get("district_info") or []
    }
    iso = next(
        (
            _clean(row.get("iso_code"))
            for row in address.get("district_info") or []
            if _clean(row.get("iso_code")).startswith("BR-")
        ),
        "",
    )
    return _finish(
        {
            "name": _clean(order.get("cpf_name")) or _clean(address.get("name")),
            "document": _digits(order.get("cpf")),
        },
        {
            "name": _clean(address.get("name")),
            "street": _clean(address.get("address_line1")) or _clean(address.get("address_detail")),
            "number": _clean(address.get("address_line2")) or "S/N",
            "complement": _clean(address.get("address_line4")),
            "district": _clean(address.get("address_line3")) or levels.get("district", ""),
            "city": levels.get("city", ""),
            "state": iso.removeprefix("BR-"),
            "postal_code": _digits(address.get("postal_code")),
            "phone": "",
        },
    )


def _uf(value):
    text = _clean(value)
    return text.upper() if UF.fullmatch(text.upper()) else UF_NAMES.get(_fold(text), "")


def _split_street(full, stops, postal_code):
    """Shopee only gives one line. Where each part sits still needs proof on a real order."""
    parts = [part.strip() for part in full.split(",") if part.strip()]
    if not parts:
        return "", "S/N", ""
    index = next((i for i, part in enumerate(parts) if i and STREET_NUMBER.fullmatch(part)), None)
    if index is None:
        return parts[0], "S/N", ""
    complement = []
    for part in parts[index + 1 :]:
        if part.casefold() in stops or (postal_code and _digits(part) == postal_code):
            break
        complement.append(part)
    number = "S/N" if not parts[index][0].isdigit() else parts[index]
    return ", ".join(parts[:index]), number, ", ".join(complement)


def from_shopee(order):
    address = order.get("recipient_address") or {}
    city = _clean(address.get("city"))
    district = _clean(address.get("district")) or _clean(address.get("town"))
    state = _uf(address.get("state"))
    postal_code = _digits(address.get("zipcode"))
    stops = {v.casefold() for v in [city, district, _clean(address.get("state"))] if v}
    street, number, complement = _split_street(
        _clean(address.get("full_address")), stops, postal_code
    )
    name = _clean(address.get("name"))
    return _finish(
        {"name": name, "document": _digits(order.get("buyer_cpf_id"))},
        {
            "name": name,
            "street": street,
            "number": number,
            "complement": complement,
            "district": district,
            "city": city,
            "state": state,
            "postal_code": postal_code,
            "phone": "",
        },
    )


READERS = {
    "ml": lambda raw: from_ml(raw.get("billing"), raw.get("shipment")),
    "shopee": from_shopee,
    "tiktok": from_tiktok,
    "magalu": from_magalu,
}


def project(channel, raw):
    if channel not in READERS:
        raise ERPError("recipient_channel", "Canal sem leitor de destinatário.", 409)
    return READERS[channel](raw)
