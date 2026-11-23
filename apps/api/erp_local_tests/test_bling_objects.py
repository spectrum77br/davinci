"""Bling's postage objects copied read only, shown as Bling's screen shows them."""

from datetime import UTC, datetime, timedelta

from app.erp_local import bling_objects, bling_shadow_probe

NOW = datetime(2026, 11, 23, 15, tzinfo=UTC)


def orders():
    return [
        {
            "id": 101,
            "number": "307578",
            "store": "205437320",
            "store_number": "2000015884543969",
            "day": "2026-11-05",
            "channel": "ml",
        },
        {
            "id": 102,
            "number": "308393",
            "store": "205857216",
            "store_number": "251105ABC",
            "day": "2026-11-11",
            "channel": "shopee",
        },
        {
            "id": 103,
            "number": "290001",
            "store": "205437320",
            "store_number": "2000000000000009",
            "day": "2026-10-20",
            "channel": "ml",
        },
    ]


def reader_for(seen, statuses):
    def reader(request):
        results = []
        for call in request["calls"]:
            path = call["path"]
            seen.append(path)
            if path == "/logisticas":
                page = dict(call["params"])["pagina"]
                rows = (
                    [
                        {
                            "id": 583444,
                            "descricao": "ML - 33",
                            "tipoIntegracao": "MercadoEnvios",
                            "situacao": "H",
                            "servicos": [{"id": 14895795451}],
                        },
                        {
                            "id": 1220011,
                            "descricao": "Logística Shopee - 15",
                            "tipoIntegracao": "LogisticaShopee",
                            "situacao": "H",
                            "servicos": [{"id": 777}],
                        },
                    ]
                    if page == "1"
                    else []
                )
                body = {"data": rows}
            elif path.startswith("/pedidos/vendas/"):
                order = path.rsplit("/", 1)[1]
                number = {"101": "307578", "102": "308393", "103": "290001"}[order]
                body = {
                    "data": {
                        "numero": number,
                        "numeroLoja": f"loja-{order}",
                        "contato": {"nome": f"Cliente {order} (apelido{order})"},
                        "transporte": {"volumes": [{"id": int(order) * 10}]},
                    }
                }
            else:
                obj = path.rsplit("/", 1)[1]
                ml = obj in {"1010", "1030"}
                body = {
                    "data": {
                        "id": int(obj),
                        "servico": {
                            "id": 14895795451 if ml else 777,
                            "nome": "Expresso (511948)" if ml else "Shopee Xpress",
                        },
                        "rastreamento": {
                            "codigo": "AE117561356BR" if ml else "BR2696447404317",
                            "descricao": "Chegou",
                            "situacao": statuses[obj],
                            "ultimaAlteracao": "2026-11-09 19:18:02",
                        },
                        "dataSaida": "2026-11-05"
                        if obj == "1010"
                        else "2026-11-11"
                        if obj == "1020"
                        else "2026-10-20",
                    }
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


def test_orders_then_objects_are_copied_and_listed_like_blings_screen(erp):
    seen, statuses = [], {"1010": 3, "1020": 8, "1030": 1}
    report = bling_objects.run(
        erp, reader_for(seen, statuses), now=NOW, orders_reader=lambda _sql: orders()
    )
    assert report["state"] == "read_ok" and report["orders"] == 3 and report["objects"] == 3
    with erp.connection() as db:
        shown = bling_objects.listing(db, date_from="2026-10-24", date_to="2026-11-23")
    assert shown["total"] == 2  # the October object is out of the 30 days
    first, second = shown["items"]
    assert (second["number"], second["service"], second["tracking"], second["situation"]) == (
        "307578",
        "Expresso (511948)",
        "AE117561356BR",
        "Entregue",
    )
    assert second["client"] == "Cliente 101 (apelido101)" and second["logistics"] == "ML - 33"
    assert (first["service"], first["situation"]) == ("Shopee Xpress", "Não postado")
    names = {item["name"]: item["objects"] for item in shown["logistics"]}
    assert names == {"ML - 33": 1, "Logística Shopee - 15": 1}
    with erp.connection() as db:
        ml_only = bling_objects.listing(
            db, logistics="583444", date_from="2026-10-24", date_to="2026-11-23"
        )
        found = bling_objects.listing(db, query="apelido102")
    assert [i["number"] for i in ml_only["items"]] == ["307578"]
    assert [i["number"] for i in found["items"]] == ["308393"]


def test_only_objects_that_may_move_are_read_again(erp):
    seen, statuses = [], {"1010": 3, "1020": 8, "1030": 1}
    reader = reader_for(seen, statuses)
    bling_objects.run(erp, reader, now=NOW, orders_reader=lambda _sql: orders())
    seen.clear()
    bling_objects.run(
        erp, reader, now=NOW + timedelta(hours=7), orders_reader=lambda _sql: orders()
    )
    # Delivered (3) never again; in transit (1) after 6 h; not tracked by Bling (8) after a day.
    assert seen == ["/logisticas/objetos/1030"]
    seen.clear()
    bling_objects.run(
        erp, reader, now=NOW + timedelta(hours=25), orders_reader=lambda _sql: orders()
    )
    # Bling's logistics are read again once a day (three pages in one read).
    assert sorted(seen) == ["/logisticas"] * 3 + [
        "/logisticas/objetos/1020",
        "/logisticas/objetos/1030",
    ]


def test_orders_out_of_the_window_leave_with_their_client_key(erp):
    bling_objects.run(
        erp,
        reader_for([], {"1010": 3, "1020": 8, "1030": 1}),
        now=NOW,
        orders_reader=lambda _sql: orders(),
    )
    later = NOW + timedelta(days=45)
    report = bling_objects.run(erp, reader_for([], {}), now=later, orders_reader=lambda _sql: [])
    assert report["forgotten"] == 3
    with erp.connection() as db:
        assert bling_objects.listing(db)["total"] == 0


def test_a_refused_read_keeps_the_last_copy(erp):
    bling_objects.run(
        erp,
        reader_for([], {"1010": 3, "1020": 8, "1030": 1}),
        now=NOW,
        orders_reader=lambda _sql: orders(),
    )

    def expired(_request):
        return {
            "version": 1,
            "source": bling_shadow_probe.SOURCE,
            "read_only": "on",
            "no_token_refresh": True,
            "state": "token_expired",
            "results": [],
        }

    more = orders() + [
        {
            "id": 104,
            "number": "1",
            "store": "x",
            "store_number": "y",
            "day": "2026-11-20",
            "channel": "ml",
        }
    ]
    report = bling_objects.run(
        erp, expired, now=NOW + timedelta(hours=30), orders_reader=lambda _sql: more
    )
    assert report["state"] == "token_expired"
    with erp.connection() as db:
        assert bling_objects.listing(db)["total"] == 3


def test_own_tracking_is_compared_with_blings(erp):
    bling_objects.run(
        erp,
        reader_for([], {"1010": 3, "1020": 8, "1030": 1}),
        now=NOW,
        orders_reader=lambda _sql: orders(),
    )
    with erp.connection() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS store_tracking (channel TEXT, store TEXT, number TEXT,"
            " data TEXT, updated_at TEXT)"
        )
        db.execute(
            "INSERT INTO store_tracking VALUES ('ml','x','loja-101','{\"tracking_number\":"
            "\"ae117561356br\"}','now')"
        )
        report = bling_objects.compare(db)
    assert report["ml"] == {"bling": 2, "same": 1, "different": 0, "unread": 1}
    assert report["shopee"] == {"bling": 1, "same": 0, "different": 0, "unread": 1}


def test_the_read_script_allows_order_detail_and_objects_only_by_id():
    ok = [
        {"path": "/logisticas", "params": [["pagina", "1"]]},
        {"path": "/logisticas/objetos/16293005561", "params": []},
        {"path": "/pedidos/vendas/27317908722", "params": []},
    ]
    assert bling_shadow_probe.checked(ok) == ok
    for path in ("/pedidos/vendas", "/logisticas/objetos", "/logisticas/remessas/1"):
        try:
            bling_shadow_probe.checked([{"path": path, "params": []}])
        except ValueError:
            continue
        raise AssertionError(path)
