"""Statement files read as they come from the bank. Importing settles nothing by itself."""

from decimal import Decimal

import pytest

from app.erp_local import bank_statements as bs
from app.erp_local.core import ERPError

OFX = """OFXHEADER:100
DATA:OFXSGML

<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKTRANLIST>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20261105120000[-3:BRT]
<TRNAMT>-100.50
<FITID>A1
<CHECKNUM>77
<MEMO>Boleto fornecedor
</STMTTRN>
<STMTTRN>
<TRNTYPE>CREDIT
<DTPOSTED>20261106
<TRNAMT>250,00
<FITID>A2
<NAME>Pix recebido
</STMTTRN>
</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>
"""


def bank(erp, identifier="b1"):
    erp.command(
        "bank-" + identifier,
        "bank.create",
        {"id": identifier, "name": "Banco", "opening": "0"},
    )


def test_ofx_without_closing_tags_keeps_sign_date_and_id():
    lines = bs.parse(OFX)
    assert [(i["line_id"], i["posted_on"], i["amount"]) for i in lines] == [
        ("A1", "2026-11-05", "-100.50"),
        ("A2", "2026-11-06", "250.00"),
    ]
    assert lines[0]["memo"] == "Boleto fornecedor" and lines[0]["document"] == "77"
    assert lines[1]["memo"] == "Pix recebido"


@pytest.mark.parametrize(
    "content",
    [
        "Data;Descrição;Valor\n05/11/2026;Boleto;-1.234,56\n",
        "date,memo,amount\n2026-11-05,Boleto,-1234.56\n",
        "05/11/2026;Boleto;-1.234,56\n",
    ],
)
def test_csv_in_the_formats_banks_use(content):
    line = bs.parse(content)[0]
    assert (line["posted_on"], line["amount"], line["memo"]) == ("2026-11-05", "-1234.56", "Boleto")


@pytest.mark.parametrize(
    "content",
    [
        "",
        "Data;Valor\nontem;10,00\n",
        "Data;Valor\n05/11/2026;muito\n",
        "<STMTTRN><MEMO>sem valor</STMTTRN>",
        "Data;Valor\n05/11/2026;1,234\n",
    ],
)
def test_unreadable_files_are_refused(content):
    with pytest.raises(ERPError):
        bs.parse(content)


def test_importing_twice_adds_nothing_and_moves_no_money(erp):
    bank(erp)
    with erp.connection() as db:
        before = db.execute("SELECT count(*) FROM finance_payments").fetchone()[0]
    first = erp.command("s1", "bank.statement.import", {"bank_id": "b1", "content": OFX})
    assert first["added"] == 2 and first["repeated"] == 0
    second = erp.command("s2", "bank.statement.import", {"bank_id": "b1", "content": OFX})
    assert second["added"] == 0 and second["repeated"] == 2
    with erp.connection() as db:
        assert db.execute("SELECT count(*) FROM finance_payments").fetchone()[0] == before
        assert db.execute("SELECT count(*) FROM finance_statement_lines").fetchone()[0] == 2


def test_unknown_account_is_refused(erp):
    with pytest.raises(ERPError, match="não encontrada"):
        erp.command("s1", "bank.statement.import", {"bank_id": "nada", "content": OFX})


def payment_candidate(monkeypatch, *rows):
    monkeypatch.setattr(bs, "_open_payments", lambda _db: list(rows))


def lines_with(erp, monkeypatch, *rows):
    payment_candidate(monkeypatch, *rows)
    with erp.connection() as db:
        return {i["line_id"]: i for i in bs.snapshot(db)["lines"]}


def paid(identifier, cash="100.50", kind="payable", on="2026-11-04", bank_id="b1"):
    return {
        "id": identifier,
        "bank_id": bank_id,
        "kind": kind,
        "cash": Decimal(cash),
        "reversed": False,
        "created_at": on,
    }


def test_suggestion_needs_exact_signed_amount_same_account_and_close_date(erp, monkeypatch):
    bank(erp)
    erp.command("s1", "bank.statement.import", {"bank_id": "b1", "content": OFX})
    assert lines_with(erp, monkeypatch, paid("p1"))["A1"]["suggestion"] == "p1"
    assert lines_with(erp, monkeypatch, paid("p1", cash="100.51"))["A1"]["suggestion"] is None
    assert lines_with(erp, monkeypatch, paid("p1", kind="receivable"))["A1"]["suggestion"] is None
    assert lines_with(erp, monkeypatch, paid("p1", on="2026-10-01"))["A1"]["suggestion"] is None
    assert lines_with(erp, monkeypatch, paid("p1", bank_id="b2"))["A1"]["suggestion"] is None


def test_real_settlement_is_found_with_its_own_date(erp):
    bank(erp)
    erp.command("s1", "bank.statement.import", {"bank_id": "b1", "content": OFX})
    erp.command(
        "e1",
        "finance.entry.create",
        {
            "kind": "payable",
            "reference": "boleto-teste",
            "party": "Fornecedor",
            "amount": "100.50",
            "issued_on": "2026-11-05",
            "competence_on": "2026-11-05",
            "due_date": "2026-11-05",
        },
    )
    with erp.connection() as db:
        installment = db.execute(
            "SELECT id FROM finance_installments ORDER BY rowid DESC LIMIT 1"
        ).fetchone()[0]
    erp.command(
        "p1",
        "finance.pay",
        {
            "installment_id": installment,
            "bank_id": "b1",
            "amount": "100.50",
            "date": "2026-11-05",
            "external_id": "pagamento-1",
        },
    )
    with erp.connection() as db:
        paid_id = db.execute("SELECT id FROM finance_payments ORDER BY rowid DESC").fetchone()[0]
        line = next(i for i in bs.snapshot(db)["lines"] if i["line_id"] == "A1")
    assert line["suggestion"] == paid_id


def test_two_possible_payments_suggest_neither(erp, monkeypatch):
    bank(erp)
    erp.command("s1", "bank.statement.import", {"bank_id": "b1", "content": OFX})
    assert lines_with(erp, monkeypatch, paid("p1"), paid("p2"))["A1"]["suggestion"] is None
