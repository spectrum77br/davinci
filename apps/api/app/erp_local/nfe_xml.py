"""NF-e 4.00 layout, access key and XML signature. No network and no fiscal decision here.

Order and format follow the authorized notes kept as reference. Tax lines arrive resolved by
nfe_rules. The signature uses the canonical form the SEFAZ checks, confirmed against the digest
and signature of an authorized note.
"""

import base64
import hashlib
import re
from decimal import ROUND_HALF_UP, Decimal
from xml.etree.ElementTree import canonicalize
from xml.sax.saxutils import escape

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import pkcs12

NFE_NS = "http://www.portalfiscal.inf.br/nfe"
SIG_NS = "http://www.w3.org/2000/09/xmldsig#"
UF_CODES = {
    "RO": "11",
    "AC": "12",
    "AM": "13",
    "RR": "14",
    "PA": "15",
    "AP": "16",
    "TO": "17",
    "MA": "21",
    "PI": "22",
    "CE": "23",
    "RN": "24",
    "PB": "25",
    "PE": "26",
    "AL": "27",
    "SE": "28",
    "BA": "29",
    "MG": "31",
    "ES": "32",
    "RJ": "33",
    "SP": "35",
    "PR": "41",
    "SC": "42",
    "RS": "43",
    "MS": "50",
    "MT": "51",
    "DF": "53",
    "GO": "52",
}
HOMOLOGATION_NAME = "NF-E EMITIDA EM AMBIENTE DE HOMOLOGACAO - SEM VALOR FISCAL"


def money(value):
    return f"{Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}"


def quantity(value):
    return f"{Decimal(str(value)).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP):.4f}"


def unit_price(value):
    text = f"{Decimal(str(value)):.10f}".rstrip("0")
    whole, _, fraction = text.partition(".")
    return whole + "." + fraction.ljust(2, "0")


def check_digit(digits):
    total, weight = 0, 2
    for digit in reversed(digits):
        total += int(digit) * weight
        weight = 2 if weight == 9 else weight + 1
    rest = total % 11
    return "0" if rest < 2 else str(11 - rest)


def access_key(uf_code, issued, cnpj, series, number, emission_type, code):
    base = (
        uf_code
        + issued[2:4]
        + issued[5:7]
        + cnpj
        + "55"
        + str(series).zfill(3)
        + str(number).zfill(9)
        + emission_type
        + code
    )
    return base + check_digit(base)


def _tag(name, value):
    return f"<{name}>{escape(str(value))}</{name}>"


def _maybe(name, value):
    return _tag(name, value) if value not in (None, "") else ""


def _address(prefix, a):
    return (
        f"<{prefix}>"
        + _tag("xLgr", a["street"])
        + _tag("nro", a["number"])
        + _maybe("xCpl", a.get("complement"))
        + _tag("xBairro", a["district"])
        + _tag("cMun", a["city_code"])
        + _tag("xMun", a["city"])
        + _tag("UF", a["uf"])
        + _tag("CEP", a["zip"])
        + _tag("cPais", "1058")
        + _tag("xPais", "Brasil")
        + _maybe("fone", a.get("phone"))
        + f"</{prefix}>"
    )


def _fields(values, order):
    return "".join(_tag(key, values[key]) for key in order if key in values)


def _imposto(tax):
    icms = tax["icms"]
    body = _maybe("vTotTrib", money(tax["approximate"]) if tax.get("approximate") else "")
    body += (
        f"<ICMS><{icms['group']}>"
        + _fields(icms, ["orig", "CST", "CSOSN", "modBC", "vBC", "pICMS", "vICMS"])
        + f"</{icms['group']}></ICMS>"
    )
    if tax.get("ipi"):
        ipi = tax["ipi"]
        body += (
            f"<IPI>{_tag('cEnq', ipi['enq'])}<IPITrib>"
            + _fields(ipi, ["CST", "vBC", "pIPI", "vIPI"])
            + "</IPITrib></IPI>"
        )
    for name, rate, value in [("PIS", "pPIS", "vPIS"), ("COFINS", "pCOFINS", "vCOFINS")]:
        line = tax[name.lower()]
        body += (
            f"<{name}><{line['group']}>"
            + _fields(line, ["CST", "vBC", rate, value])
            + f"</{line['group']}></{name}>"
        )
    if tax.get("difal"):
        body += (
            "<ICMSUFDest>"
            + _fields(
                tax["difal"],
                [
                    "vBCUFDest",
                    "vBCFCPUFDest",
                    "pFCPUFDest",
                    "pICMSUFDest",
                    "pICMSInter",
                    "pICMSInterPart",
                    "vFCPUFDest",
                    "vICMSUFDest",
                    "vICMSUFRemet",
                ],
            )
            + "</ICMSUFDest>"
        )
    return f"<imposto>{body}</imposto>"


def _item(index, item):
    product = item["product"] if "product" in item else item
    prod = (
        _tag("cProd", product["sku"])
        + _tag("cEAN", product.get("gtin") or "SEM GTIN")
        + _tag("xProd", product["name"])
        + _tag("NCM", product["ncm"])
        + _maybe("CEST", product.get("cest"))
        + _tag("CFOP", item["cfop"])
        + _tag("uCom", product["unit"])
        + _tag("qCom", quantity(item["quantity"]))
        + _tag("vUnCom", unit_price(item["unit_price"]))
        + _tag("vProd", money(item["total"]))
        + _tag("cEANTrib", product.get("gtin_tax") or product.get("gtin") or "SEM GTIN")
        + _tag("uTrib", product["unit"])
        + _tag("qTrib", quantity(item["quantity"]))
        + _tag("vUnTrib", unit_price(item["unit_price"]))
        + (_tag("vFrete", money(item["freight"])) if Decimal(item.get("freight") or 0) else "")
        + (_tag("vDesc", money(item["discount"])) if Decimal(item.get("discount") or 0) else "")
        + _tag("indTot", "1")
        + _maybe("nItemPed", item.get("order_line"))
    )
    return f'<det nItem="{index}"><prod>{prod}</prod>{_imposto(item["tax"])}</det>'


def totals(items, *, extra=0):
    sums = dict.fromkeys(
        [
            "vBC",
            "vICMS",
            "vProd",
            "vFrete",
            "vDesc",
            "vPIS",
            "vCOFINS",
            "vTotTrib",
            "vFCPUFDest",
            "vICMSUFDest",
            "vICMSUFRemet",
        ],
        Decimal(0),
    )
    difal = False
    for item in items:
        tax = item["tax"]
        sums["vProd"] += Decimal(money(item["total"]))
        sums["vFrete"] += Decimal(money(item.get("freight") or 0))
        sums["vDesc"] += Decimal(money(item.get("discount") or 0))
        sums["vBC"] += Decimal(tax["icms"].get("vBC", 0))
        sums["vICMS"] += Decimal(tax["icms"].get("vICMS", 0))
        sums["vPIS"] += Decimal(tax["pis"].get("vPIS", 0))
        sums["vCOFINS"] += Decimal(tax["cofins"].get("vCOFINS", 0))
        sums["vTotTrib"] += Decimal(money(tax.get("approximate") or 0))
        if tax.get("difal"):
            difal = True
            for key in ["vFCPUFDest", "vICMSUFDest", "vICMSUFRemet"]:
                sums[key] += Decimal(tax["difal"][key])
    sums["vNF"] = sums["vProd"] - sums["vDesc"] + sums["vFrete"] + Decimal(str(extra))
    sums["difal"] = difal
    return sums


def _total(sums):
    zero = "0.00"
    parts = _tag("vBC", money(sums["vBC"])) + _tag("vICMS", money(sums["vICMS"]))
    parts += _tag("vICMSDeson", zero)
    if sums["difal"]:
        for key in ["vFCPUFDest", "vICMSUFDest", "vICMSUFRemet"]:
            parts += _tag(key, money(sums[key]))
    parts += _tag("vFCP", zero) + _tag("vBCST", zero) + _tag("vST", zero)
    parts += _tag("vFCPST", zero) + _tag("vFCPSTRet", zero)
    parts += _tag("vProd", money(sums["vProd"])) + _tag("vFrete", money(sums["vFrete"]))
    parts += _tag("vSeg", zero) + _tag("vDesc", money(sums["vDesc"]))
    parts += _tag("vII", zero) + _tag("vIPI", zero) + _tag("vIPIDevol", zero)
    parts += _tag("vPIS", money(sums["vPIS"])) + _tag("vCOFINS", money(sums["vCOFINS"]))
    parts += _tag("vOutro", zero) + _tag("vNF", money(sums["vNF"]))
    if sums["vTotTrib"]:
        parts += _tag("vTotTrib", money(sums["vTotTrib"]))
    return f"<total><ICMSTot>{parts}</ICMSTot></total>"


def build(spec):
    """Return the unsigned <NFe>, its access key and the computed totals."""
    company, recipient, items = spec["company"], spec["recipient"], spec["items"]
    address = company["address"]
    uf_code = UF_CODES[address["uf"]]
    homologation = spec["environment"] == "homologation"
    key = access_key(
        uf_code,
        spec["issued_at"],
        company["cnpj"],
        spec["series"],
        spec["number"],
        spec.get("emission_type", "1"),
        spec["code"],
    )
    sums = totals(items)
    ide = (
        _tag("cUF", uf_code)
        + _tag("cNF", spec["code"])
        + _tag("natOp", spec["nature"])
        + _tag("mod", "55")
        + _tag("serie", spec["series"])
        + _tag("nNF", spec["number"])
        + _tag("dhEmi", spec["issued_at"])
        + _maybe("dhSaiEnt", spec.get("left_at"))
        + _tag("tpNF", spec.get("direction", "1"))
        + _tag("idDest", "1" if recipient["address"]["uf"] == address["uf"] else "2")
        + _tag("cMunFG", address["city_code"])
        + _tag("tpImp", "1")
        + _tag("tpEmis", spec.get("emission_type", "1"))
        + _tag("cDV", key[-1])
        + _tag("tpAmb", "2" if homologation else "1")
        + _tag("finNFe", spec.get("purpose", "1"))
        + _tag("indFinal", spec.get("final_consumer", "1"))
        + _tag("indPres", spec["presence"])
        + _maybe("indIntermed", spec.get("intermediary", "0"))
        + _tag("procEmi", "0")
        + _tag("verProc", spec["software"])
        + (f"<NFref>{_tag('refNFe', spec['reference'])}</NFref>" if spec.get("reference") else "")
    )
    emit = (
        _tag("CNPJ", company["cnpj"])
        + _tag("xNome", company["name"])
        + _maybe("xFant", company.get("fantasy"))
        + _address("enderEmit", address)
        + _tag("IE", company["ie"])
        + _tag("CRT", company["crt"])
    )
    document = recipient["document"]
    dest = (
        _tag("CNPJ" if len(document) == 14 else "CPF", document)
        + _tag("xNome", HOMOLOGATION_NAME if homologation else recipient["name"])
        + _address("enderDest", recipient["address"])
        + _tag("indIEDest", recipient.get("ie_indicator", "9"))
        + _maybe("IE", recipient.get("ie"))
        + _maybe("email", recipient.get("email"))
    )
    transport = spec.get("transport") or {}
    volume = ""
    if transport.get("net_weight") is not None or transport.get("gross_weight") is not None:
        volume = (
            "<vol>"
            + _maybe("qVol", transport.get("volumes"))
            + _tag("pesoL", f"{Decimal(str(transport.get('net_weight') or 0)):.3f}")
            + _tag("pesoB", f"{Decimal(str(transport.get('gross_weight') or 0)):.3f}")
            + "</vol>"
        )
    carrier = ""
    if transport.get("carrier"):
        carrier = (
            "<transporta>"
            + _tag("CNPJ", transport["carrier"]["cnpj"])
            + _tag("xNome", transport["carrier"]["name"])
            + "</transporta>"
        )
    payments = "".join(
        "<detPag>"
        + _tag("tPag", p["type"])
        + _maybe("xPag", p.get("description"))
        + _tag("vPag", money(p["amount"]))
        + "</detPag>"
        for p in spec["payments"]
    )
    technical = spec["technical"]
    inf = (
        f"<ide>{ide}</ide><emit>{emit}</emit><dest>{dest}</dest>"
        + "".join(_item(i, item) for i, item in enumerate(items, start=1))
        + _total(sums)
        + f"<transp>{_tag('modFrete', spec['freight_mode'])}{carrier}{volume}</transp>"
        + f"<pag>{payments}</pag>"
        + (f"<infAdic>{_tag('infCpl', spec['notes'])}</infAdic>" if spec.get("notes") else "")
        + "<infRespTec>"
        + _tag("CNPJ", technical["cnpj"])
        + _tag("xContato", technical["contact"])
        + _tag("email", technical["email"])
        + _tag("fone", technical["phone"])
        + "</infRespTec>"
    )
    xml = f'<NFe xmlns="{NFE_NS}"><infNFe versao="4.00" Id="NFe{key}">{inf}</infNFe></NFe>'
    return {"xml": xml, "key": key, "totals": {k: str(v) for k, v in sums.items()}}


def _reference_fragment(xml, name):
    match = re.search(rf"<{name}\b.*?</{name}>", xml, re.S)
    if not match:
        raise ValueError("Elemento ausente: " + name)
    return match.group(0)


def _with_namespace(fragment, namespace):
    opening = fragment.split(">", 1)[0]
    if "xmlns=" in opening:
        return fragment
    return fragment.replace(opening, f'{opening} xmlns="{namespace}"', 1)


def _digest(xml, tag="infNFe"):
    canonical = canonicalize(_with_namespace(_reference_fragment(xml, tag), NFE_NS))
    return base64.b64encode(hashlib.sha1(canonical.encode()).digest()).decode()  # noqa: S324


def _signed_info(identifier, digest):
    return (
        f'<SignedInfo xmlns="{SIG_NS}">'
        '<CanonicalizationMethod Algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315">'
        "</CanonicalizationMethod>"
        f'<SignatureMethod Algorithm="{SIG_NS}rsa-sha1"></SignatureMethod>'
        f'<Reference URI="#{identifier}"><Transforms>'
        f'<Transform Algorithm="{SIG_NS}enveloped-signature"></Transform>'
        '<Transform Algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315"></Transform>'
        f'</Transforms><DigestMethod Algorithm="{SIG_NS}sha1"></DigestMethod>'
        f"<DigestValue>{digest}</DigestValue></Reference></SignedInfo>"
    )


def _signature(identifier, digest, certificate, password):
    private_key, cert, _ = pkcs12.load_key_and_certificates(certificate, password)
    if private_key is None or cert is None:
        raise ValueError("Certificado sem chave privada")
    signed_info = _signed_info(identifier, digest)
    value = private_key.sign(
        canonicalize(signed_info).encode(),
        padding.PKCS1v15(),
        hashes.SHA1(),  # noqa: S303 - required by the NF-e layout
    )
    der = cert.public_bytes(serialization.Encoding.DER)
    inner = signed_info.replace(f' xmlns="{SIG_NS}"', "", 1)
    return (
        f'<Signature xmlns="{SIG_NS}">{inner}'
        f"<SignatureValue>{base64.b64encode(value).decode()}</SignatureValue>"
        f"<KeyInfo><X509Data><X509Certificate>{base64.b64encode(der).decode()}"
        "</X509Certificate></X509Data></KeyInfo></Signature>"
    )


def sign(xml, certificate, password):
    """Sign one <NFe>. The private key is used here and never leaves this call."""
    key = re.search(r'Id="NFe(\d{44})"', xml).group(1)
    signature = _signature("NFe" + key, _digest(xml), certificate, password)
    return xml.replace("</NFe>", signature + "</NFe>")


def sign_element(xml, tag, closing, certificate, password):
    """Sign an event or number gap the same way: the signature follows the signed element."""
    identifier = re.search(rf'<{tag}\b[^>]*\bId="([^"]+)"', xml).group(1)
    signature = _signature(identifier, _digest(xml, tag), certificate, password)
    return xml.replace(f"</{closing}>", signature + f"</{closing}>")


def verify(xml, tag="infNFe"):
    """True only when digest and RSA signature both match the embedded certificate."""
    try:
        stored = re.search(r"<DigestValue>(.*?)</DigestValue>", xml).group(1)
        if stored != _digest(xml, tag):
            return False
        info = _with_namespace(_reference_fragment(xml, "SignedInfo"), SIG_NS)
        value = base64.b64decode(
            re.search(r"<SignatureValue>(.*?)</SignatureValue>", xml, re.S).group(1)
        )
        der = base64.b64decode(
            re.search(r"<X509Certificate>(.*?)</X509Certificate>", xml, re.S).group(1)
        )
        cert = x509.load_der_x509_certificate(der)
        cert.public_key().verify(
            value,
            canonicalize(info).encode(),
            padding.PKCS1v15(),
            hashes.SHA1(),  # noqa: S303 - required by the NF-e layout
        )
    except (AttributeError, ValueError, InvalidSignature):
        return False
    return True


CORRECTION_TERMS = (
    "A Carta de Correcao e disciplinada pelo paragrafo 1o-A do art. 7o do Convenio S/N, de 15 de "
    "dezembro de 1970 e pode ser utilizada para regularizacao de erro ocorrido na emissao de "
    "documento fiscal, desde que o erro nao esteja relacionado com: I - as variaveis que "
    "determinam o valor do imposto tais como: base de calculo, aliquota, diferenca de preco, "
    "quantidade, valor da operacao ou da prestacao; II - a correcao de dados cadastrais que "
    "implique mudanca do remetente ou do destinatario; III - a data de emissao ou de saida."
)


MANIFESTS = {
    "210200": "Confirmacao da Operacao",
    "210210": "Ciencia da Operacao",
    "210220": "Desconhecimento da Operacao",
    "210240": "Operacao nao Realizada",
}


def event(kind, *, uf, cnpj, key, moment, sequence, environment="2", protocol=None, text=""):
    """Build a cancel, correction or recipient event, still unsigned."""
    if kind == "110111":
        detail = _tag("descEvento", "Cancelamento") + _tag("nProt", protocol) + _tag("xJust", text)
    elif kind == "110110":
        detail = (
            _tag("descEvento", "Carta de Correcao")
            + _tag("xCorrecao", text)
            + _tag("xCondUso", CORRECTION_TERMS)
        )
    elif kind in MANIFESTS:
        detail = _tag("descEvento", MANIFESTS[kind]) + (
            _tag("xJust", text) if kind == "210240" else ""
        )
    else:
        raise ValueError("Evento não suportado")
    identifier = f"ID{kind}{key}{sequence:02d}"
    return (
        f'<evento versao="1.00" xmlns="{NFE_NS}"><infEvento Id="{identifier}">'
        + _tag("cOrgao", "91" if kind in MANIFESTS else UF_CODES[uf])
        + _tag("tpAmb", environment)
        + _tag("CNPJ", cnpj)
        + _tag("chNFe", key)
        + _tag("dhEvento", moment)
        + _tag("tpEvento", kind)
        + _tag("nSeqEvento", sequence)
        + _tag("verEvento", "1.00")
        + f'<detEvento versao="1.00">{detail}</detEvento></infEvento></evento>'
    )


def number_gap(*, uf, year, cnpj, series, first, last, reason, environment="2"):
    """Build an unsigned request to void an unused number range."""
    identifier = (
        f"ID{UF_CODES[uf]}{year % 100:02d}{cnpj}55{int(series):03d}{int(first):09d}{int(last):09d}"
    )
    return (
        f'<inutNFe versao="4.00" xmlns="{NFE_NS}"><infInut Id="{identifier}">'
        + _tag("tpAmb", environment)
        + _tag("xServ", "INUTILIZAR")
        + _tag("cUF", UF_CODES[uf])
        + _tag("ano", f"{year % 100:02d}")
        + _tag("CNPJ", cnpj)
        + _tag("mod", "55")
        + _tag("serie", series)
        + _tag("nNFIni", first)
        + _tag("nNFFin", last)
        + _tag("xJust", reason)
        + "</infInut></inutNFe>"
    )
