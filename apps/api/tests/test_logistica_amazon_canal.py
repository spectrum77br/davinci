"""logistica_amazon_canal — DBA × Envio próprio e contas de prazo (puro)."""

from __future__ import annotations

from datetime import date

from app.services import logistica_amazon_canal as canal


def test_classificar_easyship_e_dba_mesmo_com_servico_correios():
    assert canal.classificar({"easyship_status": "PickedUp"}, "SEDEX") == "dba"
    assert canal.classificar({"order_status": "Shipped", "easyship_status": "Delivered"}) == "dba"


def test_classificar_mfn_sem_easyship_e_envio_proprio():
    # O pedido do print (701-3967231-6921832): MFN, "Std Dom 9", sem EasyShip.
    assert canal.classificar({"order_status": "Shipped", "fulfillment_channel": "MFN"}) == "proprio"
    assert canal.classificar({"fulfillment_channel": "MFN"}, "SEDEX") == "proprio"


def test_classificar_mfn_com_servico_dba_no_bling_e_dba():
    # DBA recém-criado: a SP-API ainda não preencheu o EasyShip, o Bling já sabe.
    assert canal.classificar({"fulfillment_channel": "MFN"}, "Logistica Amazon Dba") == "dba"


def test_classificar_afn_e_fba():
    assert canal.classificar({"order_status": "Shipped", "fulfillment_channel": "AFN"}) == "fba"


def test_classificar_so_pelo_bling_quando_sp_api_ainda_nao_respondeu():
    assert canal.classificar({}, "SEDEX") == "proprio"
    assert canal.classificar({}, "Logistica Amazon Dba") == "dba"
    assert canal.classificar({}, None) is None
    assert canal.classificar(None, "") is None


def test_canal_por_servico_bling():
    assert canal.canal_por_servico_bling("Logistica Amazon Dba") == "dba"
    assert canal.canal_por_servico_bling("PAC") == "proprio"
    assert canal.canal_por_servico_bling("  ") is None


def test_dias_uteis_apos_bate_com_a_data_de_entrega_do_bling():
    # Objeto 16221786970: dataSaida 14/09 (segunda) + prazoEntregaPrevisto 7 = 23/09.
    assert canal.dias_uteis_apos(date(2026, 9, 14), 7) == date(2026, 9, 23)
    # Sexta + 1 dia útil = segunda.
    assert canal.dias_uteis_apos(date(2026, 9, 18), 1) == date(2026, 9, 21)
    assert canal.dias_uteis_apos(date(2026, 9, 14), 0) == date(2026, 9, 14)
    assert canal.dias_uteis_apos(date(2026, 9, 14), -3) == date(2026, 9, 14)


def test_previsao_correios():
    assert canal.previsao_correios(date(2026, 9, 14), 7) == date(2026, 9, 23)
    assert canal.previsao_correios(None, 7) is None
    assert canal.previsao_correios(date(2026, 9, 14), None) == date(2026, 9, 14)


def test_eh_email_relay_amazon():
    assert canal.eh_email_relay_amazon("n340cj40yxfjsq4@marketplace.amazon.com.br")
    assert canal.eh_email_relay_amazon("X@MARKETPLACE.AMAZON.COM")
    assert not canal.eh_email_relay_amazon("rosana@gmail.com")
    assert not canal.eh_email_relay_amazon("")
    assert not canal.eh_email_relay_amazon(None)


def test_canal_exibido_sem_sinal_e_dba():
    # Vinicius 15/09: "esses sem classificação era DBA" — sem sinal = DBA.
    assert canal.canal_exibido(None) == "dba"
    assert canal.canal_exibido("proprio") == "proprio"
    assert canal.canal_exibido("fba") == "fba"
