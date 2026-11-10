"""Financial categories like Bling's: income and expense, nested, assigned to any account."""

import pytest
from test_core import run
from test_manual_finance import payload

from app.erp_local import finance_categories
from app.erp_local.core import ERPError


def account_ids(erp, kind):
    with erp.connection() as db:
        return [r[0] for r in db.execute("SELECT id FROM accounts WHERE kind=?", (kind,))]


def test_nested_categories_without_duplicates_or_cycles(erp):
    parent = run(erp, "finance.category.save", {"name": "Despesas operacionais", "kind": "expense"})
    child = run(
        erp,
        "finance.category.save",
        {"name": "Frete", "kind": "expense", "parent_id": parent["id"]},
    )
    with pytest.raises(ERPError, match="Já existe"):
        run(
            erp,
            "finance.category.save",
            {"name": "frete", "kind": "expense", "parent_id": parent["id"]},
        )
    with pytest.raises(ERPError, match="outro tipo"):
        run(
            erp,
            "finance.category.save",
            {"name": "Venda", "kind": "income", "parent_id": parent["id"]},
        )
    with pytest.raises(ERPError, match="dentro dela mesma"):
        run(
            erp,
            "finance.category.save",
            {
                "id": parent["id"],
                "version": 1,
                "name": "Despesas operacionais",
                "kind": "expense",
                "parent_id": child["id"],
            },
        )
    with erp.connection() as db:
        paths = {c["name"]: c["path"] for c in finance_categories.snapshot(db)["categories"]}
    assert paths["Frete"] == "Despesas operacionais › Frete"


def test_categories_change_on_many_accounts_on_the_right_side(erp):
    expense = run(erp, "finance.category.save", {"name": "Aluguel", "kind": "expense"})
    income = run(erp, "finance.category.save", {"name": "Vendas", "kind": "income"})
    run(erp, "finance.entry.create", payload(occurrence="once", count=1))
    run(
        erp,
        "finance.entry.create",
        payload(kind="receivable", reference="R-1", occurrence="once", count=1),
    )
    payable, receivable = account_ids(erp, "payable"), account_ids(erp, "receivable")
    run(erp, "finance.category.assign", {"account_ids": payable, "category_id": expense["id"]})
    with pytest.raises(ERPError, match="receita"):
        run(
            erp,
            "finance.category.assign",
            {"account_ids": receivable, "category_id": expense["id"]},
        )
    run(erp, "finance.category.assign", {"account_ids": receivable, "category_id": income["id"]})
    with erp.connection() as db:
        links = finance_categories.snapshot(db)["assignments"]
    assert links[payable[0]] == expense["id"] and links[receivable[0]] == income["id"]
    run(
        erp,
        "finance.category.save",
        {"id": expense["id"], "version": 1, "name": "Aluguel", "kind": "expense", "active": False},
    )
    with pytest.raises(ERPError, match="ativa"):
        run(erp, "finance.category.assign", {"account_ids": payable, "category_id": expense["id"]})
