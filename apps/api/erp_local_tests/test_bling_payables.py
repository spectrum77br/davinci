"""Bling's open payables copied read only: list, detail of what changed, supplier names."""

from datetime import UTC, datetime, timedelta

import pytest

from app.erp_local import bling_payables, bling_shadow_probe


def reader_for(bills, contacts, seen):
    def reader(request):
        results = []
        for call in request["calls"]:
            path, params = call["path"], dict(call["params"])
            seen.append(path)
            if path == "/contas/pagar":
                # As Bling: filter 4 brings partly paid bills (3), filter 3 the cancelled (5).
                wanted = {"1": "1", "4": "3", "3": "5"}[params["situacoes[]"]]
                rows = [b["list"] for b in bills if str(b["list"]["situacao"]) == wanted]
                page, size = int(params["pagina"]), int(params["limite"])
                body = {"data": rows[(page - 1) * size : page * size]}
            elif path.startswith("/contas/pagar/"):
                bill = next(b for b in bills if str(b["list"]["id"]) == path.rsplit("/", 1)[1])
                body = {"data": {**bill["list"], **bill["detail"]}}
            else:
                body = {
                    "data": {"id": path.rsplit("/", 1)[1], "nome": contacts[path.rsplit("/", 1)[1]]}
                }
            results.append({"status": 200, "body": body})
        return {
            "version": 1,
            "source": bling_shadow_probe.SOURCE,
            "read_only": "on",
            "no_token_refresh": True,
            "state": "read_ok",
            "results": results,
        }

    return reader


def bill(identifier, situation=1, value=100.0, contact=7):
    return {
        "list": {
            "id": identifier,
            "situacao": situation,
            "vencimento": "2026-11-20",
            "valor": value,
            "contato": {"id": contact},
            "formaPagamento": {"id": 0},
        },
        "detail": {
            "saldo": value / 2 if situation == 3 else value,
            "historico": f"Conta {identifier}",
            "numeroDocumento": f"DOC-{identifier}",
            "categoria": {"id": 14693438796},
            "borderos": [1] if situation == 3 else [],
            "ocorrencia": {"tipo": 3},
        },
    }


def test_open_bills_are_copied_with_detail_and_supplier_then_closed_ones_leave(erp):
    bills = [bill(1), bill(2, situation=3, value=300.0, contact=8)]
    seen = []
    report = bling_payables.run(erp, reader_for(bills, {"7": "Fornecedor A", "8": "B"}, seen))
    assert report["state"] == "read_ok" and report["open"] == 2 and report["details_read"] == 2
    with erp.connection() as db:
        copy = bling_payables.snapshot(db, today="2026-11-23")
    assert copy["totals"] == {
        "count": 2,
        "amount": 400.0,
        "balance": 250.0,
        "balance_unknown": 0,
        "overdue": 2,
    }
    first = next(r for r in copy["rows"] if r["id"] == "2")
    assert first["supplier"] == "B" and first["situation"] == "Pago parcialmente"
    assert first["payments"] == 1 and first["repeats"] and first["document"] == "DOC-2"
    # Nothing changed: no detail and no supplier read again.
    seen.clear()
    later = datetime.now(UTC) + timedelta(minutes=31)
    bling_payables.run(erp, reader_for(bills, {"7": "Fornecedor A", "8": "B"}, seen), now=later)
    assert seen and all(path == "/contas/pagar" for path in seen)
    # Paid in Bling: it leaves the open list and the copy.
    bling_payables.run(
        erp,
        reader_for(bills[:1], {"7": "Fornecedor A"}, seen),
        now=later + timedelta(minutes=31),
    )
    with erp.connection() as db:
        assert [r["id"] for r in bling_payables.snapshot(db)["rows"]] == ["1"]


def test_a_refused_read_keeps_the_last_copy(erp):
    bills = [bill(1)]
    bling_payables.run(erp, reader_for(bills, {"7": "A"}, []))

    def expired(_request):
        return {
            "version": 1,
            "source": bling_shadow_probe.SOURCE,
            "read_only": "on",
            "no_token_refresh": True,
            "state": "token_expired",
            "results": [],
        }

    later = datetime.now(UTC) + timedelta(minutes=31)
    assert bling_payables.run(erp, expired, now=later)["state"] == "token_expired"
    with erp.connection() as db:
        assert bling_payables.snapshot(db)["totals"]["count"] == 1


def test_the_read_script_allows_only_the_payables_paths_added():
    ok = [
        {"path": "/contas/pagar", "params": [["situacoes[]", "1"], ["pagina", "1"]]},
        {"path": "/contas/pagar/123", "params": []},
        {"path": "/contatos/456", "params": []},
    ]
    assert bling_shadow_probe.checked(ok) == ok
    for path in ("/contatos", "/contas/receber", "/pedidos/vendas", "/nfe/1"):
        try:
            bling_shadow_probe.checked([{"path": path, "params": []}])
        except ValueError:
            continue
        raise AssertionError(path)


def test_a_complete_copy_rests_half_an_hour_between_reads(erp):
    start = datetime(2026, 11, 23, 12, tzinfo=UTC)
    seen = []
    reader = reader_for([bill(1)], {"7": "A"}, seen)
    assert bling_payables.run(erp, reader, now=start)["state"] == "read_ok"
    seen.clear()
    later = start + timedelta(minutes=10)
    assert bling_payables.run(erp, reader, now=later) == {
        "taken_at": later.isoformat(),
        "state": "recent",
    }
    assert seen == []
    assert bling_payables.run(erp, reader, now=start + timedelta(minutes=31))["state"] == "read_ok"


def test_a_429_is_asked_again_after_a_wait(erp, monkeypatch):
    waits = []
    monkeypatch.setattr(bling_payables, "_wait", waits.append)
    inner = reader_for([bill(1)], {"7": "A"}, [])
    refused = {"left": 2}

    def busy(request):
        answer = inner(request)
        if refused["left"]:
            refused["left"] -= 1
            answer["results"][0] = {"status": 429, "body": None}
        return answer

    assert bling_payables.run(erp, busy)["state"] == "read_ok"
    assert waits == [3, 8]


def test_cancelled_bills_never_enter_the_copy(erp):
    bills = [bill(1), bill(2, situation=5)]
    assert bling_payables.run(erp, reader_for(bills, {"7": "A"}, []))["open"] == 1
    with erp.connection() as db:
        assert [r["id"] for r in bling_payables.snapshot(db)["rows"]] == ["1"]


def test_partly_paid_bills_are_read_first_and_their_balance_waits_for_the_detail(erp):
    bills = [bill(1), bill(2, situation=3, value=300.0), bill(3, situation=3, value=80.0)]
    bling_payables.run(erp, reader_for(bills, {"7": "A"}, []), max_details=1)
    with erp.connection() as db:
        rows = {r["id"]: r for r in bling_payables.snapshot(db)["rows"]}
        totals = bling_payables.snapshot(db)["totals"]
    # Read first: a partly paid bill; the open one is its value; the other partly paid is unknown.
    assert rows["2"]["detailed"] and rows["2"]["balance"] == 150.0
    assert rows["1"]["balance"] == 100.0 and rows["3"]["balance"] is None
    assert totals["balance"] == 250.0 and totals["balance_unknown"] == 1


def test_switch_imports_the_bills_once_with_bling_payments_kept_out_of_the_balance(erp):
    from app.erp_local import finance
    from app.erp_local.core import ERPError

    partial = bill(2, situation=3, value=300.0, contact=8)
    partial["detail"].update({"competencia": "2026-11-01", "categoria": {"id": 555}})
    plain = bill(1)
    plain["detail"]["categoria"] = {"id": 0}
    di = bill(3, value=900.0)
    di["detail"].update({"numeroDocumento": "mala04/2", "categoria": {"id": 14693438796}})
    bills = [plain, partial, di]
    bling_payables.run(
        erp, reader_for(bills, {"7": "Fornecedor A", "8": "Fornecedor B"}, []), max_details=1
    )
    with erp.connection() as db:
        assert bling_payables.import_preview(db)["waiting"] == 2
    with pytest.raises(ERPError, match="Ainda lendo"):
        bling_payables.import_all(erp)
    later = datetime.now(UTC) + timedelta(minutes=31)
    bling_payables.run(
        erp, reader_for(bills, {"7": "Fornecedor A", "8": "Fornecedor B"}, []), now=later
    )
    with erp.connection() as db:
        plan = bling_payables.import_preview(db)
    assert (plan["bills"], plan["waiting"], plan["amount"], plan["paid_before"]) == (
        3,
        0,
        1300.0,
        150.0,
    )
    assert plan["to_pay"] == 1150.0 and plan["stock_lots"] == 1
    assert plan["categories"] == {"Sem categoria": 1, "Bling 555": 1, "Estoque": 1}
    report = bling_payables.import_all(erp)
    assert (report["created"], report["payments_before_switch"]) == (3, 1)
    assert bling_payables.import_all(erp)["created"] == 0  # once
    with erp.connection() as db:
        lots = finance.lots(db)
        balance = finance.management(db)
        installments = finance.snapshot(db)["installments"]
    assert any(lot["lot"] == "mala04" for lot in lots)
    assert len([i for i in installments if i["kind"] == "payable"]) == 3
    # The 150 paid in Bling is not in the ERP's balance; the 150 left is a forecast.
    assert sum(float(r["paid"]) for r in balance) == 0
    assert sum(float(r["forecast"]) for r in balance) == 100.0 + 150.0  # bill 1 + rest of bill 2
    assert (
        bling_payables.run(erp, reader_for(bills, {}, []), now=later + timedelta(hours=1))["state"]
        == "imported"
    )
