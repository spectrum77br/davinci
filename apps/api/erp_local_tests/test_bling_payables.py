"""Bling's open payables copied read only: list, detail of what changed, supplier names."""

from datetime import UTC, datetime, timedelta

from app.erp_local import bling_payables, bling_shadow_probe


def reader_for(bills, contacts, seen):
    def reader(request):
        results = []
        for call in request["calls"]:
            path, params = call["path"], dict(call["params"])
            seen.append(path)
            if path == "/contas/pagar":
                rows = [
                    b["list"] for b in bills if str(b["list"]["situacao"]) == params["situacoes[]"]
                ]
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
    assert copy["totals"] == {"count": 2, "amount": 400.0, "balance": 250.0, "overdue": 2}
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
