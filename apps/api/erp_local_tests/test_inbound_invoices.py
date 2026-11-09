"""Supplier invoices with synthetic XML. Stock only moves after every line has a known product."""
# ruff: noqa: S314 - Synthetic local XML.

from decimal import Decimal

import pytest

from app.erp_local import inbound_invoices as ii
from app.erp_local import nfe_xml
from app.erp_local.core import ERPError

NS = "http://www.portalfiscal.inf.br/nfe"
SUPPLIER = "11222333000181"
BUYER = "12345678000195"
SKU = "demo.aparelho"


def key_for(number="123"):
    body = f"3126110{SUPPLIER[:7]}{SUPPLIER[7:]}55001{int(number):09d}1{12345678:08d}"[:43]
    body = body.ljust(43, "0")
    return body + nfe_xml.check_digit(body)


def note(
    number="123",
    *,
    status="100",
    environment="1",
    quantity="2.0000",
    unit_price="50.00",
    total="100.00",
    invoice="115.00",
    freight="10.00",
    ipi="5.00",
    gtin="SEM GTIN",
    code="F-1",
    bills="<dup><nDup>001</nDup><dVenc>2026-12-06</dVenc><vDup>115.00</vDup></dup>",
    operation="1",
):
    protocol = (
        f'<protNFe versao="4.00"><infProt><cStat>{status}</cStat><nProt>131</nProt></infProt>'
        "</protNFe>"
        if status
        else ""
    )
    return (
        f'<nfeProc xmlns="{NS}" versao="4.00"><NFe><infNFe Id="NFe{key_for(number)}" versao="4.00">'
        f"<ide><natOp>Venda</natOp><serie>1</serie><nNF>{number}</nNF>"
        f"<dhEmi>2026-11-05T10:00:00-03:00</dhEmi><tpNF>{operation}</tpNF>"
        f"<tpAmb>{environment}</tpAmb></ide>"
        f"<emit><CNPJ>{SUPPLIER}</CNPJ><xNome>Fornecedor Teste LTDA</xNome><IE>123</IE>"
        "<enderEmit><xLgr>Rua A</xLgr><nro>1</nro><xBairro>Centro</xBairro><cMun>3106200</cMun>"
        "<xMun>Belo Horizonte</xMun><UF>MG</UF><CEP>30110000</CEP></enderEmit></emit>"
        f"<dest><CNPJ>{BUYER}</CNPJ></dest>"
        f'<det nItem="1"><prod><cProd>{code}</cProd><cEAN>{gtin}</cEAN><xProd>Aparelho</xProd>'
        f"<NCM>85171300</NCM><CFOP>5102</CFOP><uCom>CX</uCom><qCom>{quantity}</qCom>"
        f"<vUnCom>{unit_price}</vUnCom><vProd>{total}</vProd></prod><imposto><ICMS><ICMS00>"
        "<vICMS>18.00</vICMS></ICMS00></ICMS></imposto></det>"
        f"<total><ICMSTot><vProd>{total}</vProd><vFrete>{freight}</vFrete><vSeg>0.00</vSeg>"
        f"<vDesc>0.00</vDesc><vIPI>{ipi}</vIPI><vST>0.00</vST><vOutro>0.00</vOutro>"
        f"<vNF>{invoice}</vNF></ICMSTot></total><cobr>{bills}</cobr>"
        f"</infNFe></NFe>{protocol}</nfeProc>"
    )


def imported(erp, xml=None, op="i1"):
    return erp.command(op, "inbound.import", {"xml": xml or note()})["key"]


def link(erp, key, op="l1", factor="1"):
    erp.command(op, "inbound.link", {"key": key, "line": 1, "sku": SKU, "factor": factor})


def book(erp, key, op="b1", **extra):
    return erp.command(op, "inbound.book", {"key": key, "depot": "geral", **extra})


def physical(erp):
    with erp.connection() as db:
        return erp.balance(db, SKU, "geral")[0]


def test_reading_keeps_every_figure_of_the_note():
    parsed = ii.parse(note())
    assert parsed["key"] == key_for() and parsed["authorized"] is True
    assert (
        parsed["supplier"]["document"] == SUPPLIER and parsed["supplier"]["ibge_code"] == "3106200"
    )
    assert (
        parsed["items"][0]["taxes"]["vICMS"] == "18.00" and parsed["totals"]["invoice"] == "115.00"
    )
    assert parsed["installments"][0]["due_date"] == "2026-12-06"


@pytest.mark.parametrize(
    "xml",
    [
        "isto não é xml",
        "<a/>",
        '<!DOCTYPE x><nfeProc xmlns="' + NS + '"/>',
        note().replace(key_for(), key_for()[:43] + ("0" if key_for()[43] != "0" else "1")),
    ],
)
def test_invalid_files_are_refused(erp, xml):
    with pytest.raises(ERPError):
        erp.command("bad", "inbound.import", {"xml": xml})


def test_same_key_with_another_file_is_a_conflict_and_same_file_is_a_repeat(erp):
    assert erp.command("i1", "inbound.import", {"xml": note()})["duplicate"] is False
    assert erp.command("i2", "inbound.import", {"xml": note()})["duplicate"] is True
    with pytest.raises(ERPError, match="outro XML"):
        erp.command("i3", "inbound.import", {"xml": note(freight="20.00", invoice="125.00")})


def test_importing_moves_nothing_and_booking_needs_every_line_linked(erp):
    before = physical(erp)
    key = imported(erp)
    assert physical(erp) == before
    with pytest.raises(ERPError, match="Vincule"):
        book(erp, key)
    assert physical(erp) == before


def test_booking_creates_purchase_stock_and_bills_at_the_note_total(erp):
    before = physical(erp)
    key = imported(erp)
    link(erp, key)
    result = book(erp, key)
    assert physical(erp) == before + 2
    with erp.connection() as db:
        doc, data = erp.document(db, result["purchase_id"], "purchase")
        accounts = db.execute(
            "SELECT count(*) FROM accounts WHERE document_id=? AND kind='payable'", (doc["id"],)
        ).fetchone()[0]
        contact = db.execute("SELECT data FROM own_contacts").fetchall()
    assert Decimal(data["total"]) == Decimal("115.00") and data["stock_posted"] is True
    assert data["installments"] == [{"due_date": "2026-12-06", "amount": "115.00"}]
    assert accounts == 1 and any(SUPPLIER in row[0] for row in contact)
    assert key in data["internal_notes"]


def test_booking_twice_changes_nothing(erp):
    key = imported(erp)
    link(erp, key)
    first = book(erp, key)
    during = physical(erp)
    again = book(erp, key, op="b2")
    assert again["duplicate"] is True and again["purchase_id"] == first["purchase_id"]
    assert physical(erp) == during


def test_box_conversion_counts_units_and_the_link_is_reused_on_the_next_note(erp):
    before = physical(erp)
    key = imported(erp)
    link(erp, key, factor="12")
    book(erp, key)
    assert physical(erp) == before + 24
    second = imported(erp, note("124"), op="i2")
    with erp.connection() as db:
        known = ii.suggestions(db, ii.parse(note("124")))
    assert known[1] == {"sku": SKU, "factor": "12", "source": "link"}
    book(erp, second, op="b2")
    assert physical(erp) == before + 48


def test_rounding_of_the_conversion_stays_on_record_and_total_matches(erp):
    key = imported(
        erp,
        note(quantity="3.0000", unit_price="33.33", total="99.99", invoice="114.99"),
    )
    link(erp, key, factor="7")
    result = book(erp, key)
    with erp.connection() as db:
        _, data = erp.document(db, result["purchase_id"], "purchase")
    assert Decimal(data["total"]) == Decimal("114.99")


def test_installments_that_do_not_close_become_one_on_the_issue_date(erp):
    key = imported(
        erp, note(bills="<dup><nDup>1</nDup><dVenc>2026-12-06</dVenc><vDup>1.00</vDup></dup>")
    )
    link(erp, key)
    result = book(erp, key)
    with erp.connection() as db:
        _, data = erp.document(db, result["purchase_id"], "purchase")
    assert data["installments"] == [{"due_date": "2026-11-05", "amount": "115.00"}]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"status": ""}, "autorização"),
        ({"status": "110"}, "autorização"),
        ({"environment": "2"}, "homologação"),
        ({"operation": "0"}, "devolução"),
    ],
)
def test_note_that_cannot_enter_stock_is_refused(erp, kwargs, message):
    before = physical(erp)
    key = imported(erp, note(**kwargs))
    link(erp, key)
    with pytest.raises(ERPError, match=message):
        book(erp, key)
    assert physical(erp) == before


def test_note_addressed_to_someone_else_is_refused_when_companies_exist(erp):
    with erp.connection() as db:
        db.execute(
            "INSERT INTO fiscal_companies VALUES (?,?,?,?)",
            ("c1", 1, '{"cnpj":"99888777000166"}', "2026-11-06"),
        )
    key = imported(erp)
    link(erp, key)
    with pytest.raises(ERPError, match="destinada"):
        book(erp, key)


def test_gtin_suggestion_only_when_it_fits_one_product(erp):
    with erp.connection() as db:
        rows = db.execute("SELECT sku FROM product_details LIMIT 1").fetchall()
    erp.command(
        "g1", "product.update", {"sku": SKU, "version": 0, "data": {"gtin": "4006381333931"}}
    )
    parsed = ii.parse(note(gtin="4006381333931"))
    with erp.connection() as db:
        assert ii.suggestions(db, parsed)[1] == {"sku": SKU, "factor": "1", "source": "gtin"}
    assert rows is not None


def test_kit_cannot_receive_a_line(erp):
    key = imported(erp)
    with pytest.raises(ERPError, match="peças"):
        erp.command("k1", "inbound.link", {"key": key, "line": 1, "sku": "demo.kit", "factor": "1"})


def test_rejected_note_never_books_and_booked_note_cannot_be_rejected(erp):
    key = imported(erp)
    link(erp, key)
    erp.command("r1", "inbound.reject", {"key": key})
    with pytest.raises(ERPError, match="Recusada|recusada"):
        book(erp, key)
    other = imported(erp, note("125"), op="i2")
    book(erp, other, op="b2")
    with pytest.raises(ERPError, match="Estorne"):
        erp.command("r2", "inbound.reject", {"key": other})


def test_choosing_stock_only_leaves_bills_for_later(erp):
    key = imported(erp)
    link(erp, key)
    result = book(erp, key, post_accounts=False)
    with erp.connection() as db:
        _, data = erp.document(db, result["purchase_id"], "purchase")
    assert data["stock_posted"] is True and data["accounts_posted"] is False
