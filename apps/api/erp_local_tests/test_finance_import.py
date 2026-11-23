"""Accounts from a spreadsheet: preview writes nothing, import once, the same file never doubles."""

from fastapi.testclient import TestClient

from app.erp_local import finance_import
from app.erp_local.api import create_app

CSV = (
    "Tipo;Cliente/Fornecedor;Valor;Vencimento;Histórico;Categoria;Celular;Mala\n"
    "Pagar;Fornecedor A;1.234,56;10/12/2026;Aluguel dezembro;Aluguel;60;40\n"
    "Receber;Cliente B;99,90;2026-12-15;Venda balcão;;;\n"
    "Pagar;Fornecedor C;abc;10/12/2026;Valor errado;;;\n"
    "Pagar;Fornecedor D;10,00;31/02/2026;Data errada;;;\n"
)


def manual_entries(erp):
    with erp.connection() as db:
        return db.execute("SELECT count(*) FROM documents WHERE kind='financial'").fetchone()[0]


def test_preview_tries_every_line_and_writes_nothing(erp):
    lines = finance_import.parse(CSV)
    result = finance_import.preview(erp, lines)
    assert [r["ok"] for r in result] == [True, True, False, False]
    assert (
        result[0]["payload"]["amount"] == "1234.56" and result[1]["payload"]["kind"] == "receivable"
    )
    assert result[3]["error"]
    assert manual_entries(erp) == 0


def test_import_once_and_the_same_file_again_adds_nothing(erp):
    first = finance_import.commit(erp, finance_import.parse(CSV))
    assert first["imported"] == 2 and len(first["refused"]) == 2
    count = manual_entries(erp)
    again = finance_import.commit(erp, finance_import.parse(CSV))
    assert manual_entries(erp) == count and again["imported"] == 2
    preview = finance_import.preview(erp, finance_import.parse(CSV))
    assert preview[0]["duplicate"] is True


def test_route_previews_then_imports(tmp_path):
    with TestClient(create_app(tmp_path / "i.sqlite3"), base_url="http://127.0.0.1:8765") as c:
        headers = {"X-ERP-Local": "1", "Content-Type": "text/plain"}
        preview = c.post("/api/erp-local/finance/import", content=CSV.encode(), headers=headers)
        assert preview.status_code == 200 and preview.json()["valid"] == 2
        done = c.post(
            "/api/erp-local/finance/import?commit=1", content=CSV.encode(), headers=headers
        )
        assert done.json()["imported"] == 2
        missing = c.post(
            "/api/erp-local/finance/import", content=b"Nome;Valor\nA;1\n", headers=headers
        )
        assert missing.status_code >= 400


def test_imported_expense_needs_category_and_split_and_stock_needs_its_lot(erp):
    """Novos procedimentos 13 on the spreadsheet too: same rules as the screen."""
    text = (
        "Tipo;Fornecedor;Valor;Vencimento;Categoria;Celular;Eletro;Lote\n"
        "Pagar;Sem rateio;10,00;10/12/2026;Frete;;;\n"
        "Pagar;Sem categoria;10,00;10/12/2026;;100;;\n"
        "Pagar;Com rateio;10,00;10/12/2026;Frete;30;70;\n"
        "Pagar;Lote;900,00;10/12/2026;Estoque;;;mala04\n"
    )
    results = finance_import.preview(erp, finance_import.parse(text))
    assert [bool(r.get("error")) for r in results] == [True, True, False, False]
