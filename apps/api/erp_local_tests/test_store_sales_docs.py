"""A store sale in detail and the papers from "Mais ações": they only read what was read."""

import json

import pytest
from test_store_sales import observe

from app.erp_local import store_sales_docs
from app.erp_local.core import ERPError

PICK = {"channel": "ml", "store": "conta", "number": "D1"}


def keep_recipient(erp, external):
    data = {
        "buyer": {"name": "Comprador Teste", "document": "12345678909"},
        "recipient": {
            "name": "Destinatário Teste",
            "street": "Rua Um",
            "number": "10",
            "complement": "",
            "district": "Centro",
            "city": "São Paulo",
            "state": "SP",
            "postal_code": "01001000",
            "phone": "",
        },
    }
    with erp.connection() as db:
        db.execute("SELECT 1 FROM native_order_recipients LIMIT 1")
        columns = [r[1] for r in db.execute("PRAGMA table_info(native_order_recipients)")]
        values = {
            "channel": "ml",
            "store": "conta",
            "external_id": external,
            "data": json.dumps(data),
            "ready": 1,
            "missing": "[]",
            "captured_at": "2026-11-09T10:05:00",
        }
        db.execute(
            f"INSERT INTO native_order_recipients({','.join(columns)}) "  # noqa: S608
            f"VALUES ({','.join('?' * len(columns))})",
            [values.get(c, "x") for c in columns],
        )


def test_detail_shows_items_totals_and_kept_buyer(erp):
    observe(erp, "D1", "paid")
    keep_recipient(erp, "D1")
    with erp.connection() as db:
        sale = store_sales_docs.as_json(store_sales_docs.detail(db, "ml", "conta", "D1"))
    assert sale["store"] == "ML Teste" and sale["orders"] == ["D1"]
    assert sale["item_count"] == 1 and sale["quantity_sum"] == "1"
    assert sale["items_total"] == "10.00" and sale["order_total"] == "10.00"
    assert sale["adjustment"] == "0.00"
    assert sale["buyer"]["name"] == "Comprador Teste" and sale["recipient"]["city"] == "São Paulo"
    assert sale["missing"] == []


def test_detail_without_buyer_says_so(erp):
    observe(erp, "D1", "paid")
    with erp.connection() as db:
        sale = store_sales_docs.detail(db, "ml", "conta", "D1")
        with pytest.raises(ERPError):
            store_sales_docs.detail(db, "ml", "conta", "nao-existe")
    assert sale["buyer"] == {} and sale["missing"] == ["comprador não guardado"]


def test_sheet_and_addresses_are_semicolon_csv(erp):
    observe(erp, "D1", "paid")
    keep_recipient(erp, "D1")
    with erp.connection() as db:
        sheet = store_sales_docs.sheet_csv(db, [PICK])
        addresses = store_sales_docs.addresses_csv(db, [PICK])
    assert sheet.startswith("﻿Número;Data;Loja") and "D1;09/11/2026;ML Teste" in sheet
    assert "10,00" in sheet
    assert "Destinatário Teste;12345678909;Rua Um;10;;Centro;São Paulo;SP;01001000" in addresses


def test_production_sums_by_code_and_declaration_has_both_sides(erp):
    observe(erp, "D1", "paid")
    observe(erp, "D2", "paid")
    keep_recipient(erp, "D1")
    picks = [PICK, {**PICK, "number": "D2"}]
    with erp.connection() as db:
        report = store_sales_docs.production_html(db, picks)
        papers = store_sales_docs.declaration_html(db, picks)
    assert "2 vendas selecionadas" in report and "<td>2</td><td>2</td>" in report
    assert papers.count('class="declaration"') == 2
    assert "Destinatário Teste" in papers and "Remetente" in papers
    assert "contribuinte" not in papers


def test_papers_refuse_bad_selection_and_change_nothing(erp):
    observe(erp, "D1", "paid")
    with erp.connection() as db:
        before = db.execute("SELECT count(*) FROM documents").fetchone()[0]
        for bad in [[], "x", [{"channel": "ml"}], [PICK] * 201]:
            with pytest.raises(ERPError):
                store_sales_docs.sheet_csv(db, bad)
        store_sales_docs.declaration_html(db, [PICK])
        assert db.execute("SELECT count(*) FROM documents").fetchone()[0] == before
