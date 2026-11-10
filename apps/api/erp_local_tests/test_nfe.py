"""NF-e layout, signature and flow, with synthetic data and mocked SEFAZ. No network."""
# ruff: noqa: S105, S106, S314 - Synthetic certificates and local reference XML.

import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from test_core import balance, run

from app.erp_local import fiscal_invoices, nfe_rules, nfe_sefaz, nfe_xml
from app.erp_local.core import ERPError

REFERENCE = Path(__file__).resolve().parents[3] / "data/erp-local/reference-nfe-2009.xml"
NS = "{http://www.portalfiscal.inf.br/nfe}"
CNPJ_A = "11222333000181"
CNPJ_TECH = "11444777000161"


def certificate(password=b"senha-sintetica"):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "EMPRESA TESTE")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    return pkcs12.serialize_key_and_certificates(
        b"teste", key, cert, None, serialization.BestAvailableEncryption(password)
    ), password


def company_data(crt="4", **extra):
    return {
        "cnpj": CNPJ_A,
        "name": "EMPRESA TESTE LTDA",
        "fantasy": "TESTE",
        "ie": "111222333444",
        "crt": crt,
        "series": 1,
        "next_number": 50,
        "address": {
            "street": "Rua Teste",
            "number": "10",
            "district": "Centro",
            "city_code": "3550308",
            "city": "Sao Paulo",
            "uf": "SP",
            "zip": "01001000",
            "phone": "1133334444",
        },
        "technical": {
            "cnpj": CNPJ_TECH,
            "contact": "Responsavel Tecnico",
            "email": "tecnico@example.test",
            "phone": "1133335555",
        },
        **extra,
    }


def contact(uf="MG", city="3106200", contributor="9"):
    return {
        "id": "c1",
        "name": "Cliente Teste",
        "document": "52998224725",
        "contributor": contributor,
        "state_registration": "",
        "postal_code": "30110000",
        "state": uf,
        "ibge_code": city,
        "city": "Belo Horizonte",
        "district": "Centro",
        "street": "Rua Exemplo",
        "number": "20",
        "complement": "",
        "phone": "31999990000",
        "email": "cliente@example.test",
    }


def order_data(**extra):
    return {
        "items": [
            {"sku": "a", "quantity": "1.000", "unit_price": "191.00"},
            {"sku": "b", "quantity": "2.000", "unit_price": "10.50"},
        ],
        "product_snapshots": {
            sku: {
                "name": "Produto " + sku,
                "unit": "UN",
                "gtin": "",
                "gtin_tax": "",
                "tax": {
                    "data": {"ncm": "42021210", "origin": "0", "approximate_tax_percent": None}
                },
                "net_weight_kg": "1.000",
                "gross_weight_kg": "1.500",
            }
            for sku in ["a", "b"]
        },
        "contact": contact(),
        "discount": "0",
        "freight": "0",
        "total": "212.00",
        "notes": "",
        **extra,
    }


def spec_for(crt="4", **order):
    company = company_data(crt)
    spec, reasons = fiscal_invoices.build_spec(
        company,
        order_data(**order),
        number=7,
        code="12345678",
        issued_at="2026-11-06T10:00:00-03:00",
    )
    assert reasons == []
    return spec


@pytest.mark.skipif(not REFERENCE.exists(), reason="Nota de referência não está nesta máquina")
def test_authorized_note_signature_is_reproduced_and_tampering_detected():
    raw = REFERENCE.read_text("utf-8")
    assert nfe_xml.verify(raw) is True
    assert nfe_xml.verify(raw.replace("<vNF>", "<vNF>9", 1)) is False


@pytest.mark.skipif(not REFERENCE.exists(), reason="Nota de referência não está nesta máquina")
def test_builder_reproduces_the_authorized_note_byte_for_byte():
    raw = REFERENCE.read_text("utf-8")
    original = re.search(r"<infNFe\b.*?</infNFe>", raw, re.S).group(0)
    qualified = (
        original
        if "xmlns=" in original.split(">", 1)[0]
        else original.replace("<infNFe ", f'<infNFe xmlns="{nfe_xml.NFE_NS}" ', 1)
    )
    inf = ET.fromstring(qualified)

    def _q(path):
        return "/".join(NS + part for part in path.split("/"))

    def value(node, path):
        found = node.find(_q(path))
        return found.text if found is not None else None

    def address(node):
        return {
            "street": value(node, "xLgr"),
            "number": value(node, "nro"),
            "complement": value(node, "xCpl"),
            "district": value(node, "xBairro"),
            "city_code": value(node, "cMun"),
            "city": value(node, "xMun"),
            "uf": value(node, "UF"),
            "zip": value(node, "CEP"),
            "phone": value(node, "fone"),
        }

    ide, emit, dest = inf.find(_q("ide")), inf.find(_q("emit")), inf.find(_q("dest"))
    items = []
    for det in inf.findall(_q("det")):
        prod = det.find(_q("prod"))
        imposto = det.find(_q("imposto"))
        icms = imposto.find(_q("ICMS"))[0]
        pis = imposto.find(_q("PIS"))[0]
        cofins = imposto.find(_q("COFINS"))[0]
        strip = lambda tag: tag.replace(NS, "")  # noqa: E731
        items.append(
            {
                "sku": value(prod, "cProd"),
                "name": value(prod, "xProd"),
                "ncm": value(prod, "NCM"),
                "cest": value(prod, "CEST"),
                "unit": value(prod, "uCom"),
                "gtin": value(prod, "cEAN"),
                "gtin_tax": value(prod, "cEANTrib"),
                "cfop": value(prod, "CFOP"),
                "quantity": value(prod, "qCom"),
                "unit_price": value(prod, "vUnCom"),
                "total": value(prod, "vProd"),
                "discount": value(prod, "vDesc") or "0",
                "freight": value(prod, "vFrete") or "0",
                "order_line": value(prod, "nItemPed"),
                "tax": {
                    "approximate": value(imposto, "vTotTrib"),
                    "icms": {"group": strip(icms.tag), **{strip(c.tag): c.text for c in icms}},
                    "pis": {"group": strip(pis.tag), **{strip(c.tag): c.text for c in pis}},
                    "cofins": {
                        "group": strip(cofins.tag),
                        **{strip(c.tag): c.text for c in cofins},
                    },
                },
            }
        )
    vol = inf.find(_q("transp/vol"))
    pag = inf.find(_q("pag"))
    tech = inf.find(_q("infRespTec"))
    spec = {
        "environment": "production",
        "company": {
            "cnpj": value(emit, "CNPJ"),
            "name": value(emit, "xNome"),
            "fantasy": value(emit, "xFant"),
            "ie": value(emit, "IE"),
            "crt": value(emit, "CRT"),
            "address": address(emit.find(_q("enderEmit"))),
        },
        "recipient": {
            "document": value(dest, "CPF") or value(dest, "CNPJ"),
            "name": value(dest, "xNome"),
            "ie_indicator": value(dest, "indIEDest"),
            "email": value(dest, "email"),
            "address": address(dest.find(_q("enderDest"))),
        },
        "items": items,
        "series": value(ide, "serie"),
        "number": value(ide, "nNF"),
        "code": value(ide, "cNF"),
        "issued_at": value(ide, "dhEmi"),
        "left_at": value(ide, "dhSaiEnt"),
        "nature": value(ide, "natOp"),
        "presence": value(ide, "indPres"),
        "freight_mode": value(inf, "transp/modFrete"),
        "software": value(ide, "verProc"),
        "payments": [
            {"type": value(p, "tPag"), "amount": value(p, "vPag")}
            for p in pag.findall(_q("detPag"))
        ],
        "transport": {"net_weight": value(vol, "pesoL"), "gross_weight": value(vol, "pesoB")},
        "notes": value(inf, "infAdic/infCpl"),
        "technical": {
            "cnpj": value(tech, "CNPJ"),
            "contact": value(tech, "xContato"),
            "email": value(tech, "email"),
            "phone": value(tech, "fone"),
        },
    }
    built = nfe_xml.build(spec)
    generated = re.search(r"<infNFe\b.*?</infNFe>", built["xml"], re.S).group(0)
    assert ET.canonicalize(
        generated.replace("<infNFe ", f'<infNFe xmlns="{nfe_xml.NFE_NS}" ', 1)
    ) == ET.canonicalize(qualified)
    assert built["key"] == inf.get("Id")[3:]


def test_access_key_check_digit_follows_module_11():
    assert nfe_xml.check_digit("3526110000000000000055001000000001100000001") in set("0123456789")
    key = nfe_xml.access_key("35", "2026-11-06", CNPJ_A, 1, 7, "1", "12345678")
    assert len(key) == 44 and key[:6] == "352611" and key[20:22] == "55"
    assert key[-1] == nfe_xml.check_digit(key[:-1])


def test_mei_profile_matches_what_the_group_issues():
    spec = spec_for("4")
    built = nfe_xml.build(spec)
    xml = built["xml"]
    assert spec["nature"] == "Venda de mercadoria" and spec["presence"] == "9"
    assert "<CFOP>6102</CFOP>" in xml and "<CSOSN>102</CSOSN>" in xml
    assert "<PISOutr><CST>49</CST>" in xml and "<tPag>01</tPag>" in xml
    assert "<modFrete>0</modFrete>" in xml and "<tpAmb>2</tpAmb>" in xml
    assert nfe_xml.HOMOLOGATION_NAME in xml and "Cliente Teste" not in xml
    assert "<vNF>212.00</vNF>" in xml and "<idDest>2</idDest>" in xml


def test_simples_and_normal_regimes_use_observed_rates_and_difal():
    simples = nfe_xml.build(spec_for("1"))["xml"]
    assert "<CFOP>6108</CFOP>" in simples and "<PISOutr><CST>99</CST>" in simples
    assert "<tPag>99</tPag><xPag>Outros</xPag>" in simples and "<modFrete>2</modFrete>" in simples
    normal = nfe_xml.build(spec_for("3"))
    xml = normal["xml"]
    assert "<ICMS00><orig>0</orig><CST>00</CST><modBC>3</modBC>" in xml
    assert "<pICMS>12.00</pICMS><vICMS>22.92</vICMS>" in xml
    assert "<pICMSUFDest>18.00</pICMSUFDest><pICMSInter>12.00</pICMSInter>" in xml
    assert "<vBCUFDest>204.98</vBCUFDest>" in xml and "<vICMSUFDest>13.98</vICMSUFDest>" in xml
    assert Decimal(normal["totals"]["vICMS"]) == Decimal("25.44")
    assert "<vICMSUFDest>15.52</vICMSUFDest><vICMSUFRemet>0.00</vICMSUFRemet>" in xml
    assert "<IPI><cEnq>999</cEnq><IPITrib><CST>99</CST>" in xml
    local = fiscal_invoices.build_spec(
        company_data("3"),
        order_data(contact=contact("SP", "3550308")),
        number=1,
        code="12345678",
        issued_at="2026-11-06T10:00:00-03:00",
    )[0]
    xml = nfe_xml.build(local)["xml"]
    assert "<CFOP>5102</CFOP>" in xml and "<pICMS>18.00</pICMS>" in xml
    assert "ICMSUFDest" not in xml and "<idDest>1</idDest>" in xml


@pytest.mark.parametrize(
    ("destination", "base", "own", "expected_base", "expected_share"),
    [
        # Values taken from authorized notes, so the rule is checked against reality.
        ("AM", "98.08", "6.87", "114.02", "15.94"),
        ("AL", "4.40", "0.31", "5.15", "0.75"),
        ("PE", "13.08", "0.92", "15.30", "2.22"),
        ("BA", "23.28", "1.63", "23.28", "3.14"),
        ("PR", "8.44", "1.01", "8.44", "0.63"),
        ("SC", "4.59", "0.55", "4.59", "0.23"),
    ],
)
def test_difal_matches_authorized_notes_in_both_base_modes(
    destination, base, own, expected_base, expected_share
):
    _, tax = nfe_rules.item_tax(
        company_data("3"), "SP", destination, base=Decimal(base), origin="0", contributor=False
    )
    assert tax["icms"]["vICMS"] == own
    assert tax["difal"]["vBCUFDest"] == expected_base
    assert tax["difal"]["vICMSUFDest"] == expected_share


def test_state_without_observed_difal_base_stops():
    with pytest.raises(ValueError, match="base de DIFAL"):
        nfe_rules.item_tax(
            company_data("3"), "SP", "AC", base=Decimal(10), origin="0", contributor=False
        )


def test_authorized_note_prints_with_the_existing_danfe_generator(erp, invoicing):
    from app.services.danfe_render import gerar_pdf

    prepared = run(
        erp,
        "fiscal.invoice.prepare",
        {"order_id": invoicing["order"], "company_id": invoicing["company"]},
    )
    data, password = certificate()
    client, _ = sefaz_mock([prot("100", "Autorizado o uso da NF-e", "135260000000001")])
    fiscal_invoices.transmit(erp, prepared["id"], client, data, password)
    final = invoice_row(erp, prepared["id"])["authorized_xml"]
    assert gerar_pdf(final.encode("utf-8"))[:4] == b"%PDF"


def test_unobserved_combination_stops_instead_of_inventing_tax():
    with pytest.raises(ValueError, match="Origem"):
        nfe_rules.item_tax(
            company_data("3"), "SP", "MG", base=Decimal(10), origin="1", contributor=False
        )
    with pytest.raises(ValueError, match="UF de origem"):
        nfe_rules.item_tax(
            company_data("3"), "SC", "MG", base=Decimal(10), origin="0", contributor=False
        )
    with pytest.raises(ValueError, match="Regime"):
        nfe_rules.item_tax({"crt": "2"}, "SP", "MG", base=Decimal(10), origin="0")


def test_discount_and_freight_split_keep_the_note_exactly_equal_to_the_order():
    spec = spec_for("4", discount="10.01", freight="3.33", total="205.32")
    sums = nfe_xml.totals(spec["items"])
    assert sums["vDesc"] == Decimal("10.01") and sums["vFrete"] == Decimal("3.33")
    assert sums["vNF"] == Decimal("205.32")
    xml = nfe_xml.build(spec)["xml"]
    assert "<vDesc>10.01</vDesc>" in xml and "<vNF>205.32</vNF>" in xml


def test_missing_data_is_listed_and_no_note_is_built():
    order = order_data(contact=None)
    _, reasons = fiscal_invoices.build_spec(
        company_data(), order, number=1, code="12345678", issued_at="2026-11-06T10:00:00-03:00"
    )
    assert any("cliente" in r for r in reasons)
    order = order_data()
    order["contact"] = {**contact(), "ibge_code": "", "city": "Cidade Inventada", "document": "111"}
    order["product_snapshots"]["a"]["tax"]["data"]["ncm"] = ""
    order["product_snapshots"]["b"]["type"] = "service"
    _, reasons = fiscal_invoices.build_spec(
        company_data(), order, number=1, code="12345678", issued_at="2026-11-06T10:00:00-03:00"
    )
    text = " ".join(reasons)
    assert "IBGE" in text and "CPF" in text and "NCM" in text and "serviço" in text


def test_signature_round_trip_and_tampering():
    data, password = certificate()
    xml = nfe_xml.build(spec_for("4"))["xml"]
    signed = nfe_xml.sign(xml, data, password)
    assert nfe_xml.verify(signed) is True
    assert "<Signature " in signed and signed.index("</infNFe>") < signed.index("<Signature ")
    assert nfe_xml.verify(signed.replace("<vNF>212.00", "<vNF>1.00")) is False
    with pytest.raises(ValueError):
        nfe_xml.sign(xml, data, b"senha-errada")


def sefaz_mock(replies):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text=replies.pop(0))

    return nfe_sefaz.Sefaz("SP", transport=httpx.MockTransport(handler)), calls


def prot(code, message, protocol=""):
    return (
        "<soap:Envelope xmlns:soap='http://www.w3.org/2003/05/soap-envelope'><soap:Body>"
        f"<retEnviNFe><cStat>104</cStat><protNFe versao='4.00'><infProt><cStat>{code}</cStat>"
        f"<xMotivo>{message}</xMotivo><nProt>{protocol}</nProt>"
        "<dhRecbto>2026-11-06T10:00:01-03:00</dhRecbto></infProt></protNFe></retEnviNFe>"
        "</soap:Body></soap:Envelope>"
    )


def test_production_cannot_be_reached_from_this_module():
    with pytest.raises(nfe_sefaz.ProductionClosedError):
        nfe_sefaz.Sefaz("SP", environment="production")
    client = nfe_sefaz.Sefaz("SP", transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    client.urls = {"authorize": "https://nfe.fazenda.sp.gov.br/ws/nfeautorizacao4.asmx"}
    with pytest.raises(nfe_sefaz.ProductionClosedError):
        client.authorize("<NFe/>")


def test_soap_request_and_outcome_mapping():
    client, calls = sefaz_mock([prot("100", "Autorizado o uso da NF-e", "135260000000001")])
    result = client.authorize("<NFe>x</NFe>")
    assert result["outcome"] == "authorized" and result["protocol"] == "135260000000001"
    body = calls[0].content.decode()
    assert "<indSinc>1</indSinc><NFe>x</NFe>" in body and "NFeAutorizacao4" in body
    assert calls[0].headers["content-type"].startswith("application/soap+xml")
    assert "homologacao.nfe.fazenda.sp.gov.br" in str(calls[0].url)
    assert nfe_sefaz.outcome("539") == "consult" and nfe_sefaz.outcome("204") == "consult"
    assert nfe_sefaz.outcome("105") == "pending" and nfe_sefaz.outcome(None) == "pending"
    assert nfe_sefaz.outcome("110") == "denied" and nfe_sefaz.outcome("225") == "rejected"


@pytest.fixture
def invoicing(erp):
    company = run(erp, "fiscal.company.save", {"data": company_data("4")})["id"]
    contact_id = run(
        erp,
        "contact.save",
        {
            "data": {
                "name": "Cliente Teste",
                "roles": ["customer"],
                "person_type": "F",
                "document": "52998224725",
                "postal_code": "30110000",
                "state": "MG",
                "ibge_code": "3106200",
                "city": "Belo Horizonte",
                "district": "Centro",
                "street": "Rua Exemplo",
                "number": "20",
            }
        },
    )["id"]
    run(
        erp,
        "product.update",
        {
            "sku": "demo.aparelho",
            "version": 0,
            "data": {"unit": "UN"},
            "tax": {"version": 0, "data": {"ncm": "42021210", "origin": "0"}, "reason": "teste"},
        },
    )
    created = run(
        erp,
        "order.save",
        {
            "channel": "Teste",
            "store": "A",
            "external_id": "NF-1",
            "depot": "geral",
            "customer_name": "Cliente Teste",
            "contact_id": contact_id,
            "date": "2026-11-06",
            "status": "reserved",
            "items": [{"sku": "demo.aparelho", "quantity": "1", "unit_price": "100"}],
        },
    )
    return {"company": company, "order": created["id"]}


def invoice_row(erp, identifier):
    with erp.connection() as db:
        return dict(
            db.execute("SELECT * FROM fiscal_invoices WHERE id=?", (identifier,)).fetchone()
        )


def test_preparing_numbers_once_and_touches_no_stock(erp, invoicing):
    before = balance(erp)
    first = run(
        erp,
        "fiscal.invoice.prepare",
        {"order_id": invoicing["order"], "company_id": invoicing["company"]},
    )
    assert first["number"] == 50 and first["status"] == "draft" and len(first["access_key"]) == 44
    assert balance(erp) == before
    again = run(
        erp,
        "fiscal.invoice.prepare",
        {"order_id": invoicing["order"], "company_id": invoicing["company"]},
    )
    assert again["id"] == first["id"] and again["number"] == 50
    with erp.connection() as db:
        company = fiscal_invoices.snapshot(db)["companies"][0]["data"]
    assert company["next_number"] == 51
    with pytest.raises(ERPError, match="não pode voltar"):
        run(
            erp,
            "fiscal.company.save",
            {"id": invoicing["company"], "version": 1, "data": company_data("4", next_number=10)},
        )


def test_transmit_records_intent_first_and_authorizes_once(erp, invoicing):
    prepared = run(
        erp,
        "fiscal.invoice.prepare",
        {"order_id": invoicing["order"], "company_id": invoicing["company"]},
    )
    data, password = certificate()
    client, calls = sefaz_mock([prot("100", "Autorizado o uso da NF-e", "135260000000001")])
    result = fiscal_invoices.transmit(erp, prepared["id"], client, data, password)
    assert result["status"] == "authorized"
    row = invoice_row(erp, prepared["id"])
    assert (
        row["authorized_xml"].startswith("<nfeProc") and "135260000000001" in row["authorized_xml"]
    )
    assert nfe_xml.verify(row["signed_xml"]) is True
    again = fiscal_invoices.transmit(erp, prepared["id"], client, data, password)
    assert again["duplicate"] is True and len(calls) == 1
    with pytest.raises(ERPError, match="já tem nota"):
        run(
            erp,
            "fiscal.invoice.prepare",
            {"order_id": invoicing["order"], "company_id": invoicing["company"]},
        )


def test_lost_reply_consults_before_any_resend(erp, invoicing):
    prepared = run(
        erp,
        "fiscal.invoice.prepare",
        {"order_id": invoicing["order"], "company_id": invoicing["company"]},
    )
    data, password = certificate()

    def down(_request):
        raise httpx.ConnectError("sem rede")

    client = nfe_sefaz.Sefaz("SP", transport=httpx.MockTransport(down))
    with pytest.raises(httpx.ConnectError):
        fiscal_invoices.transmit(erp, prepared["id"], client, data, password)
    assert invoice_row(erp, prepared["id"])["status"] == "sending"
    client, calls = sefaz_mock([prot("100", "Autorizado o uso da NF-e", "135260000000002")])
    result = fiscal_invoices.transmit(erp, prepared["id"], client, data, password)
    assert result["status"] == "authorized" and len(calls) == 1
    assert "consSitNFe" in calls[0].content.decode()


def test_not_found_on_consult_resends_the_same_signed_note(erp, invoicing):
    prepared = run(
        erp,
        "fiscal.invoice.prepare",
        {"order_id": invoicing["order"], "company_id": invoicing["company"]},
    )
    data, password = certificate()

    def down(_request):
        raise httpx.ConnectError("sem rede")

    with pytest.raises(httpx.ConnectError):
        fiscal_invoices.transmit(
            erp,
            prepared["id"],
            nfe_sefaz.Sefaz("SP", transport=httpx.MockTransport(down)),
            data,
            password,
        )
    signed = invoice_row(erp, prepared["id"])["signed_xml"]
    client, calls = sefaz_mock(
        [
            "<cStat>217</cStat><xMotivo>NF-e nao consta na base</xMotivo>",
            prot("100", "Autorizado o uso da NF-e", "135260000000003"),
        ]
    )
    assert (
        fiscal_invoices.transmit(erp, prepared["id"], client, data, password)["status"]
        == "authorized"
    )
    assert signed in calls[1].content.decode()


def test_rejection_keeps_number_and_allows_correction(erp, invoicing):
    prepared = run(
        erp,
        "fiscal.invoice.prepare",
        {"order_id": invoicing["order"], "company_id": invoicing["company"]},
    )
    data, password = certificate()
    client, _ = sefaz_mock([prot("225", "Falha no Schema XML")])
    result = fiscal_invoices.transmit(erp, prepared["id"], client, data, password)
    assert result["status"] == "rejected"
    row = invoice_row(erp, prepared["id"])
    assert row["authorized_xml"] is None and "Schema" in row["data"]
    again = run(
        erp,
        "fiscal.invoice.prepare",
        {"order_id": invoicing["order"], "company_id": invoicing["company"]},
    )
    assert again["id"] == prepared["id"] and again["number"] == prepared["number"]
    assert again["access_key"] != prepared["access_key"]
    assert invoice_row(erp, prepared["id"])["status"] == "draft"


def test_authorized_without_protocol_is_refused(erp, invoicing):
    prepared = run(
        erp,
        "fiscal.invoice.prepare",
        {"order_id": invoicing["order"], "company_id": invoicing["company"]},
    )
    data, password = certificate()
    signed = nfe_xml.sign(invoice_row(erp, prepared["id"])["xml"], data, password)
    with pytest.raises(ERPError, match="assinatura"):
        run(
            erp,
            "fiscal.invoice.mark_sending",
            {"id": prepared["id"], "signed_xml": signed.replace("<vNF>", "<vNF>9")},
        )
    run(erp, "fiscal.invoice.mark_sending", {"id": prepared["id"], "signed_xml": signed})
    with pytest.raises(ERPError, match="protocolo"):
        run(
            erp,
            "fiscal.invoice.record",
            {
                "id": prepared["id"],
                "code": "100",
                "message": "ok",
                "protocol": None,
                "protocol_xml": None,
                "outcome": "authorized",
            },
        )


def authorized_note(erp, invoicing):
    prepared = run(
        erp,
        "fiscal.invoice.prepare",
        {"order_id": invoicing["order"], "company_id": invoicing["company"]},
    )
    data, password = certificate()
    client, _ = sefaz_mock([prot("100", "Autorizado o uso da NF-e", "135260000000001")])
    fiscal_invoices.transmit(erp, prepared["id"], client, data, password)
    return prepared, data, password


def event_reply(code, message, protocol=""):
    return (
        f"<retEnvEvento><retEvento versao='1.00'><infEvento><cStat>{code}</cStat>"
        f"<xMotivo>{message}</xMotivo><nProt>{protocol}</nProt></infEvento></retEvento>"
        "</retEnvEvento>"
    )


def test_cancel_signs_an_event_and_only_the_sefaz_answer_changes_the_note(erp, invoicing):
    prepared, data, password = authorized_note(erp, invoicing)
    before = balance(erp)
    with pytest.raises(ERPError, match="15 caracteres"):
        fiscal_invoices.cancel(erp, prepared["id"], "curta", None, data, password)
    client, _ = sefaz_mock([event_reply("501", "Prazo de cancelamento superior")])
    refused = fiscal_invoices.cancel(
        erp, prepared["id"], "Pedido cancelado pelo cliente", client, data, password
    )
    assert refused["accepted"] is False and refused["status"] == "authorized"
    client, calls = sefaz_mock([event_reply("135", "Evento registrado", "135260000000009")])
    done = fiscal_invoices.cancel(
        erp, prepared["id"], "Pedido cancelado pelo cliente", client, data, password
    )
    assert done["accepted"] is True and done["status"] == "cancelled"
    body = calls[0].content.decode()
    assert "<tpEvento>110111</tpEvento>" in body and "<nProt>135260000000001</nProt>" in body
    assert "NFeRecepcaoEvento4" in body
    signed = re.search(r"<evento\b.*?</evento>", body, re.S).group(0)
    assert nfe_xml.verify(signed, "infEvento") is True
    assert balance(erp) == before
    with pytest.raises(ERPError, match="autorizada"):
        fiscal_invoices.cancel(
            erp, prepared["id"], "Segunda tentativa invalida", client, data, password
        )


def test_correction_letter_grows_sequence_and_carries_the_terms(erp, invoicing):
    prepared, data, password = authorized_note(erp, invoicing)
    client, calls = sefaz_mock(
        [
            event_reply("135", "Evento registrado", "1"),
            event_reply("135", "Evento registrado", "2"),
        ]
    )
    fiscal_invoices.correct(
        erp, prepared["id"], "Corrigir o complemento do endereco", client, data, password
    )
    fiscal_invoices.correct(
        erp, prepared["id"], "Corrigir o bairro do destinatario", client, data, password
    )
    assert "<nSeqEvento>1</nSeqEvento>" in calls[0].content.decode()
    second = calls[1].content.decode()
    assert "<nSeqEvento>2</nSeqEvento>" in second and "<tpEvento>110110</tpEvento>" in second
    assert "Convenio S/N" in second and f"ID110110{prepared['access_key']}02" in second
    assert invoice_row(erp, prepared["id"])["status"] == "authorized"


def test_voiding_numbers_refuses_used_ones_and_signs_the_range(erp, invoicing):
    prepared, data, password = authorized_note(erp, invoicing)
    with pytest.raises(ERPError, match="pertence"):
        fiscal_invoices.void_numbers(
            erp, invoicing["company"], 50, 52, "Falha na numeracao do sistema", None, data, password
        )
    client, calls = sefaz_mock(
        ["<retInutNFe><cStat>102</cStat><xMotivo>Inutilizado</xMotivo></retInutNFe>"]
    )
    result = fiscal_invoices.void_numbers(
        erp,
        invoicing["company"],
        51,
        52,
        "Falha na numeracao do sistema",
        client,
        data,
        password,
        year=2026,
    )
    assert result["accepted"] is True
    body = calls[0].content.decode()
    assert "<nNFIni>51</nNFIni><nNFFin>52</nNFFin>" in body
    assert f"ID3526{CNPJ_A}55001000000051000000052" in body
    assert nfe_xml.verify(body, "infInut") is True
    assert "NFeInutilizacao4" in body and "homologacao" in str(calls[0].url)


def test_city_code_comes_from_the_official_table_when_the_register_lacks_it():
    order = order_data(contact={**contact(), "ibge_code": ""})
    spec, reasons = fiscal_invoices.build_spec(
        company_data(), order, number=1, code="12345678", issued_at="2026-11-06T10:00:00-03:00"
    )
    assert reasons == [] and spec["recipient"]["address"]["city_code"] == "3106200"


def authorized(erp, invoicing):
    prepared = run(
        erp,
        "fiscal.invoice.prepare",
        {"order_id": invoicing["order"], "company_id": invoicing["company"]},
    )
    data, password = certificate()
    client, _ = sefaz_mock([prot("100", "Autorizado o uso da NF-e", "135260000000001")])
    fiscal_invoices.transmit(erp, prepared["id"], client, data, password)
    return prepared["id"], invoice_row(erp, prepared["id"])


def test_return_note_refers_to_the_original_and_is_an_entry(erp, invoicing):
    original, row = authorized(erp, invoicing)
    result = run(erp, "fiscal.invoice.prepare_return", {"invoice_id": original})
    xml = invoice_row(erp, result["id"])["xml"]
    assert f"<refNFe>{row['access_key']}</refNFe>" in xml
    assert "<finNFe>4</finNFe>" in xml and "<tpNF>0</tpNF>" in xml
    assert "<natOp>Devolucao de venda</natOp>" in xml
    assert "<tPag>90</tPag>" in xml and "<vPag>0.00</vPag>" in xml
    assert "<indPres>0</indPres>" in xml and "indIntermed" not in xml
    assert "ICMSUFDest" not in xml
    assert re.search(r"<CFOP>(1202|2202|1411|2411)</CFOP>", xml)
    assert result["key"] != row["access_key"]


def test_return_note_gets_its_own_number_and_keeps_the_sale_untouched(erp, invoicing):
    original, row = authorized(erp, invoicing)
    result = run(erp, "fiscal.invoice.prepare_return", {"invoice_id": original})
    fresh = invoice_row(erp, result["id"])
    assert fresh["number"] == row["number"] + 1
    assert invoice_row(erp, original)["status"] == "authorized"


def test_partial_returns_cannot_exceed_what_was_sold(erp, invoicing):
    original, _ = authorized(erp, invoicing)
    with erp.connection() as db:
        _, order = erp.document(db, invoicing["order"], "order")
    sold = order["items"][0]["quantity"]
    with pytest.raises(ERPError, match="maior que a vendida"):
        run(
            erp,
            "fiscal.invoice.prepare_return",
            {"invoice_id": original, "lines": [{"line": 0, "quantity": str(int(float(sold)) + 1)}]},
        )
    run(erp, "fiscal.invoice.prepare_return", {"invoice_id": original})
    with pytest.raises(ERPError, match="por inteiro"):
        run(erp, "fiscal.invoice.prepare_return", {"invoice_id": original}, key="outra")


def test_only_an_authorized_sale_accepts_a_return(erp, invoicing):
    prepared = run(
        erp,
        "fiscal.invoice.prepare",
        {"order_id": invoicing["order"], "company_id": invoicing["company"]},
    )
    with pytest.raises(ERPError, match="autorizada"):
        run(erp, "fiscal.invoice.prepare_return", {"invoice_id": prepared["id"]})


def test_sale_note_can_still_be_prepared_after_a_return_of_another_order(erp, invoicing):
    original, _ = authorized(erp, invoicing)
    result = run(erp, "fiscal.invoice.prepare_return", {"invoice_id": original})
    with pytest.raises(ERPError, match="já tem nota"):
        run(
            erp,
            "fiscal.invoice.prepare",
            {"order_id": invoicing["order"], "company_id": invoicing["company"]},
            key="repete",
        )
    assert invoice_row(erp, result["id"])["status"] == "draft"


def test_order_transport_and_intermediary_reach_the_note():
    spec = spec_for(
        "4",
        transport={
            "freight_mode": "1",
            "volumes": "2",
            "gross_weight": "1.5",
            "carrier": "Transp X",
            "carrier_cnpj": "11222333000181",
        },
        intermediary={"cnpj": "03007331000141", "account": "VENDEDOR-1"},
    )
    xml = nfe_xml.build(spec)["xml"]
    assert "<modFrete>1</modFrete>" in xml and "<qVol>2</qVol>" in xml
    assert "<pesoB>1.500</pesoB>" in xml and "<xNome>Transp X</xNome>" in xml
    assert "<indIntermed>1</indIntermed>" in xml
    assert "<infIntermed><CNPJ>03007331000141</CNPJ><idCadIntTran>VENDEDOR-1</idCadIntTran>" in xml
