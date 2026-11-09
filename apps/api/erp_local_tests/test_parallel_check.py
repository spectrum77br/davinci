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
