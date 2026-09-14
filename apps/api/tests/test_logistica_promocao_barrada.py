"""Regra da aba Status não marca como enviado o que ainda está no galpão.

Eduardo, 10/09/2026: "ela fica mudando para em andamento, antes de ser
enviado". A regra Amazon "Enviado | PendingDropOff" → Em andamento promovia
justamente o pacote que NÓS ainda temos de levar ao ponto de entrega.
"""

from __future__ import annotations

import pytest

from app.services.logistica_bling import pacote_ainda_com_o_vendedor as ainda_aqui


@pytest.mark.parametrize(
    ("plataforma", "status", "esperado", "porque"),
    [
        # Amazon: "Shipped" vira verdade quando a NF sai; quem sabe é o EasyShip.
        ("Amazon", {"order_status": "Shipped", "easyship_status": "PendingDropOff"}, True,
         "somos nós que temos de levar ao ponto de entrega"),
        ("Amazon", {"order_status": "Shipped", "easyship_status": "PendingPickUp"}, True,
         "transportadora ainda não veio buscar"),
        ("Amazon", {"order_status": "Unshipped", "easyship_status": "PendingSchedule"}, True,
         "nem enviado a Amazon considera"),
        ("Amazon", {"order_status": "Shipped", "easyship_status": "PickedUp"}, False,
         "coletado de verdade"),
        ("Amazon", {"order_status": "Shipped", "easyship_status": "DroppedOff"}, False,
         "entregue no ponto"),
        ("Amazon", {"order_status": "Shipped"}, True,
         "Shipped sem EasyShip não comprova saída do vendedor"),
        ("Amazon", {"order_status": "Shipped", "easyship_status": None}, True,
         "296762: EasyShip nulo não pode ser confundido com FBA"),
        ("Amazon", {"order_status": "Shipped", "fulfillment_channel": "MFN"}, True,
         "vendedor precisa de confirmação física"),
        ("Amazon", {"order_status": "Shipped", "fulfillment_channel": "AFN"}, False,
         "FBA explícito, enviado pela Amazon"),
        ("Amazon", {"order_status": "Unshipped", "fulfillment_channel": "AFN"}, True,
         "FBA ainda não enviado"),
        ("Amazon", {"order_status": "Shipped", "easyship_status": "PendingDropOff",
                    "fulfillment_channel": "AFN"}, True,
         "AFN não ignora EasyShip contraditório"),
        ("Amazon", {"order_status": "Shipped", "easyship_status": "UNKNOWN"}, True,
         "estado desconhecido não confirma saída"),
        # Mercado Livre: etiqueta impressa não é envio (incidente de 26/05).
        ("Mercado Livre", {"ship_status": "ready_to_ship", "ship_substatus": "printed"}, True,
         "etiqueta impressa, pacote parado"),
        ("Mercado Livre",
         {"ship_status": "ready_to_ship", "ship_substatus": "invoice_pending"},
         True, "estado que causou os 335 falsos positivos"),
        ("Mercado Livre", {"ship_status": "ready_to_ship", "ship_substatus": "dropped_off"}, False,
         "entregue na agência"),
        ("Mercado Livre", {"ship_status": "shipped", "ship_substatus": "printed"}, False,
         "o ML já diz enviado no nível do envio"),
        # Shopee e TikTok: vocabulário próprio, mesma ideia.
        ("Shopee", {"order_status": "PROCESSED"}, True, "etiqueta gerada, sem coleta"),
        ("Shopee", {"order_status": "SHIPPED"}, False, "coletado"),
        ("TikTok", {"order_status": "AWAITING_COLLECTION"}, True, "embalado esperando coleta"),
        ("TikTok", {"order_status": "IN_TRANSIT"}, False, "andando"),
        # Amazon precisa de confirmação; demais plataformas mantêm a regra.
        ("Amazon", {}, True, "sem dado não se inventa confirmação"),
        ("Mercado Livre", {}, False, "sem dado"),
        ("Magalu", {"order_status": "QUALQUER"}, False, "plataforma sem leitura"),
        (None, {"order_status": "PROCESSED"}, False, "linha sem plataforma"),
    ],
)
def test_quando_a_plataforma_diz_que_o_pacote_nao_saiu(plataforma, status, esperado, porque):
    assert ainda_aqui(plataforma, status) is esperado, porque
