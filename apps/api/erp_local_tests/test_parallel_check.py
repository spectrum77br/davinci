"""The comparison on synthetic sales: counts only, identity never in clear."""

from app.erp_local import parallel_check as pc
from app.erp_local import parallel_check_read as pr

ROW = {
    "numeroloja": "P1",
    "item_codigo": "dg053.ci",
    "item_quantidade": 1,
    "total": "100.00",
    "documento_destinatario": "529.982.247-25",
    "nome_destinatario": " Ana  Souza ",
    "cep_destino": "30110-000",
}


def sale(**extra):
    return {
        "channel": "ml",
        "items": {"dg053.ci": 1},
        "total": "100.00",
        "document": pc.fingerprint("52998224725", digits=True),
        "name": pc.fingerprint("ana souza"),
        "postal_code": pc.fingerprint("30110000", digits=True),
        **extra,
    }


def test_both_sides_hash_the_same_way_and_keep_no_clear_identity():
    bling = pr.summarize([ROW])
    assert bling["P1"]["document"] == sale()["document"] and bling["P1"]["name"] == sale()["name"]
    assert "Ana" not in str(bling) and "52998224725" not in str(bling)


def test_equal_sale_counts_equal_everywhere():
    report = pc.compare({"P1": sale()}, pr.summarize([ROW]))["ml"]
    assert report["itens_iguais"] == report["total_igual"] == report["documento_igual"] == 1
    assert report["nome_igual"] == report["cep_igual"] == 1


def test_lot_swap_is_counted_apart_from_a_real_difference():
    bling = pr.summarize([{**ROW, "item_codigo": "dg053.sp"}])
    assert pc.compare({"P1": sale()}, bling)["ml"]["itens_iguais_com_troca_de_lote"] == 1
    other = pr.summarize([{**ROW, "item_codigo": "dg054.sp"}])
    assert pc.compare({"P1": sale()}, other)["ml"]["itens_diferentes"] == 1


def test_missing_buyer_and_missing_sale_are_reported():
    report = pc.compare({"P1": sale(document=""), "P2": sale()}, pr.summarize([ROW]))["ml"]
    assert report["sem_comprador_no_erp"] == 1 and report["fora_do_bling"] == 1


def test_different_total_and_cep_show_up():
    report = pc.compare({"P1": sale(total="90.00", postal_code="1")}, pr.summarize([ROW]))["ml"]
    assert report["total_diferente"] == 1 and report["cep_diferente"] == 1


def test_total_comes_from_the_first_item_row():
    rows = [{**ROW, "item_index": 1, "total": "5.00"}, {**ROW, "item_index": 0, "total": "100.00"}]
    assert pr.summarize(rows)["P1"]["total"] == "100.00"


def test_binary_float_total_is_read_in_cents():
    row = {**ROW, "total": 479.66000000000002501110429875552654266357421875}
    assert pr.summarize([row])["P1"]["total"] == "479.66"


def test_empty_field_on_bling_is_a_gap_not_a_difference():
    bling = pr.summarize([{**ROW, "cep_destino": "", "documento_destinatario": None}])
    report = pc.compare({"P1": sale()}, bling)["ml"]
    assert report["cep_vazio_no_bling"] == 1 and report["documento_vazio_no_bling"] == 1
    assert "cep_diferente" not in report


def test_buyer_name_counts_when_bling_keeps_the_buyer():
    other = sale(name=pc.fingerprint("Outra Pessoa"), buyer_name=pc.fingerprint("ana souza"))
    assert pc.compare({"P1": other}, pr.summarize([ROW]))["ml"]["nome_igual"] == 1


def test_ml_sale_without_freight_read_is_counted_apart():
    report = pc.compare({"P1": sale(total=None)}, pr.summarize([ROW]))["ml"]
    assert report["total_sem_frete_lido"] == 1
    assert "total_diferente" not in report and "total_igual" not in report


def test_balance_at_the_hour_of_the_sale_in_any_time_zone(erp):
    import json as _json

    from app.erp_local import parallel_check, stock_check

    with erp.connection() as db:
        db.executescript(stock_check.SCHEMA)
        db.execute(
            "CREATE TABLE IF NOT EXISTS reference_rows (kind TEXT, id TEXT, data TEXT, search TEXT)"
        )
        db.execute(
            "INSERT INTO reference_rows VALUES ('products','1',?,'')",
            (_json.dumps({"bling_product_id": "7", "sku": "DG1.SP"}),),
        )
        for taken, balance in (
            ("2026-11-09T16:00:00+00:00", "0"),
            ("2026-11-09T18:00:00+00:00", "4"),
        ):
            db.execute(
                "INSERT INTO stock_snapshots VALUES (?,?)", (taken, _json.dumps({"7": balance}))
            )
        at = parallel_check.balance_at(db)
    # 14:30 in Brazil is 17:30 UTC: the 16:00 photo, when the Flex lot was empty.
    assert at("2026-11-09T14:30:00.000-03:00") == {"dg1.sp": "0"}
    assert at("2026-11-09T18:30:00Z") == {"dg1.sp": "4"}
    assert at("2026-11-09T10:00:00+00:00") is None and at("") is None
