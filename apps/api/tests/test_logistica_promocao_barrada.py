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
        ("Amazon", {"order_status": "Shipped"}, False,
         "sem EasyShip o order_status manda"),
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
        # Sem informação NÃO barra: melhor deixar a regra do usuário passar do
        # que travar plataforma que a gente não lê bem.
        ("Amazon", {}, False, "sem dado"),
        ("Mercado Livre", {}, False, "sem dado"),
        ("Magalu", {"order_status": "QUALQUER"}, False, "plataforma sem leitura"),
        (None, {"order_status": "PROCESSED"}, False, "linha sem plataforma"),
    ],
)
def test_quando_a_plataforma_diz_que_o_pacote_nao_saiu(plataforma, status, esperado, porque):
    assert ainda_aqui(plataforma, status) is esperado, porque
