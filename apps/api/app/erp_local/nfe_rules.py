"""Fiscal profile of each regime, taken from the authorized notes the group already issues.

A combination never observed stops the note with a clear reason instead of guessing a tax.
"""

import json
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from app.erp_local.nfe_xml import money

RATES = json.loads(Path(__file__).with_name("nfe_rates.json").read_text("utf-8"))
PROFILES = {
    "4": {
        "nature": "Venda de mercadoria",
        "presence": "9",
        "payment": "01",
        "freight_mode": "0",
        "pis_cst": "49",
    },
    "1": {
        "nature": "Venda de mercadorias",
        "presence": "2",
        "payment": "99",
        "freight_mode": "2",
        "pis_cst": "99",
    },
    "3": {
        "nature": "Venda de mercadorias",
        "presence": "2",
        "payment": "99",
        "freight_mode": "2",
        "pis_cst": "99",
    },
}


def _round(value):
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


IPI_ZERO = {"enq": "999", "CST": "99", "vBC": "0.00", "pIPI": "0.00", "vIPI": "0.00"}


def _pis_cofins(company, cst):
    if company.get("pis_mode") == "nt":
        return (
            {"group": "PISNT", "CST": "07"},
            {"group": "COFINSNT", "CST": "07"},
        )
    zero = {"vBC": "0.00"}
    return (
        {"group": "PISOutr", "CST": cst, **zero, "pPIS": "0.0000", "vPIS": "0.00"},
        {"group": "COFINSOutr", "CST": cst, **zero, "pCOFINS": "0.0000", "vCOFINS": "0.00"},
    )


def item_tax(company, origin_uf, destination_uf, *, base, origin, contributor=False):
    """Return (cfop, tax lines) or raise ValueError with the reason shown to the operator."""
    crt = str(company["crt"])
    profile = PROFILES.get(crt)
    if not profile:
        raise ValueError("Regime tributário sem perfil homologado: CRT " + crt)
    interstate = origin_uf != destination_uf
    pis, cofins = _pis_cofins(company, profile["pis_cst"])
    if crt == "3":
        if str(origin) != "0":
            raise ValueError("Origem de mercadoria sem alíquota observada no regime normal.")
        table = RATES["origin"].get(origin_uf)
        if not table:
            raise ValueError("UF de origem sem alíquotas cadastradas: " + origin_uf)
        difal = None
        if interstate:
            destination = table["destinations"].get(destination_uf)
            if not destination:
                raise ValueError("UF de destino sem alíquotas cadastradas: " + destination_uf)
            rate = Decimal(destination["inter"])
            if not contributor:
                own = base * rate / 100
                target = Decimal(destination["dest"])
                if destination.get("difal_base") == "double":
                    # The base is rebuilt with the destination rate inside it. Only the final
                    # values round: the authorized notes match this and not the rounded steps.
                    base_dest = (base - own) / (1 - target / 100)
                    share = _round(base_dest * target / 100 - own)
                elif destination.get("difal_base") == "single":
                    base_dest = base
                    share = _round(base * (target - rate) / 100)
                else:
                    raise ValueError("UF sem base de DIFAL observada: " + destination_uf)
                difal = {
                    "vBCUFDest": money(base_dest),
                    "vBCFCPUFDest": "0.00",
                    "pFCPUFDest": destination["fcp"],
                    "pICMSUFDest": destination["dest"],
                    "pICMSInter": destination["inter"],
                    "pICMSInterPart": "100",
                    "vFCPUFDest": "0.00",
                    "vICMSUFDest": money(share),
                    "vICMSUFRemet": "0.00",
                }
        else:
            rate = Decimal(table["internal"])
        icms = {
            "group": "ICMS00",
            "orig": str(origin),
            "CST": "00",
            "modBC": "3",
            "vBC": money(base),
            "pICMS": f"{rate:.2f}",
            "vICMS": money(_round(base * rate / 100)),
        }
        cfop = ("6108" if not contributor else "6102") if interstate else "5102"
        return cfop, {"icms": icms, "ipi": IPI_ZERO, "pis": pis, "cofins": cofins, "difal": difal}
    icms = {"group": "ICMSSN102", "orig": str(origin), "CSOSN": "102"}
    if crt == "1":
        cfop = ("6108" if not contributor else "6102") if interstate else "5102"
    else:
        cfop = "6102" if interstate else "5102"
    tax = {"icms": icms, "pis": pis, "cofins": cofins}
    if crt == "1":
        tax["ipi"] = IPI_ZERO
    return cfop, tax
