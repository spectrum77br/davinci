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


# ── Envio próprio: a Amazon nunca confirma; os Correios é que viram o pacote ──
# 18/09/2026: regra "amazon | Enviado / Em digitação → Em andamento" barrada
# pra sempre nos pedidos 296762 e 297371 (SEDEX, "Objeto em transferência"),
# porque MFN não tem EasyShip e o "Enviado" da Amazon nasce com a NF.

_PROPRIO = {"order_status": "Shipped", "fulfillment_channel": "MFN"}


@pytest.mark.parametrize(
    ("status", "correios_saiu", "esperado", "porque"),
    [
        (_PROPRIO, True, False, "Envio próprio com movimentação nos Correios: saiu"),
        (_PROPRIO, False, True, "Envio próprio sem evento dos Correios: só a NF saiu"),
        ({"order_status": "Shipped"}, True, False,
         "sem fulfillment_channel também é MFN sem EasyShip"),
        ({"order_status": "Shipped", "easyship_status": "PendingDropOff"}, True, True,
         "DBA: com EasyShip presente a palavra da Amazon continua mandando"),
        ({"order_status": "Unshipped", "fulfillment_channel": "MFN"}, True, True,
         "Amazon nem diz Enviado — Correios não substituem a confirmação do pedido"),
        ({}, True, True, "sem leitura da Amazon não se inventa confirmação"),
    ],
)
def test_correios_confirmam_saida_so_no_envio_proprio(status, correios_saiu, esperado, porque):
    assert ainda_aqui("Amazon", status, correios_saiu=correios_saiu) is esperado, porque


def test_correios_saiu_nao_muda_as_outras_plataformas():
    # A evidência dos Correios é só pra quem não tem como confirmar (Amazon MFN).
    ml = {"ship_status": "ready_to_ship", "ship_substatus": "printed"}
    assert ainda_aqui("Mercado Livre", ml, correios_saiu=True) is True
    assert ainda_aqui("Shopee", {"order_status": "PROCESSED"}, correios_saiu=True) is True


def _linha(**kw):
    from datetime import UTC, datetime

    from app.models import Logistica

    base = {
        "plataforma": "Amazon",
        "meli_status": dict(_PROPRIO),
        "rastreio": "AD929596725BR",
        "localizacao": "Indaiatuba/SP — Objeto em transferência - por favor aguarde",
        "localizacao_at": datetime(2026, 9, 18, 10, 0, tzinfo=UTC),
        "entregue_em": None,
    }
    base.update(kw)
    return Logistica(**base)


@pytest.mark.parametrize(
    ("kw", "esperado", "porque"),
    [
        ({}, True, "evento físico dos Correios (em transferência)"),
        ({"localizacao_at": None}, False,
         "localizacao_at vazio = Localização ainda é o proxy da Amazon (cidade do comprador)"),
        ({"localizacao": "BR — Etiqueta emitida"}, False,
         "só a etiqueta: o rastreio existe, mas o pacote não foi postado"),
        ({"localizacao": "Tres Marias/MG — Objeto ainda não chegou à unidade"}, True,
         "evento de trânsito (visto em produção), não é pré-postagem"),
        ({"localizacao": "BR — Etiqueta emitida", "entregue_em": "x"}, True,
         "entrega carimbada vale mesmo com Localização velha"),
    ],
)
def test_correios_confirmam_saida(kw, esperado, porque):
    from datetime import UTC, datetime

    from app.services.logistica_bling import correios_confirmam_saida

    if kw.get("entregue_em") == "x":
        kw["entregue_em"] = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
    assert correios_confirmam_saida(_linha(**kw)) is esperado, porque


@pytest.mark.parametrize(
    ("loc", "esperado"),
    [
        ("BR — Etiqueta emitida", True),
        ("Sao Paulo/SP — Pré-postagem registrada", True),
        ("Objeto aguardando postagem pelo remetente", True),
        ("Sao Paulo/SP — Objeto postado", False),
        ("Indaiatuba/SP — Objeto em transferência - por favor aguarde", False),
        ("Tres Marias/MG — Objeto ainda não chegou à unidade", False),
        (None, False),
    ],
)
def test_evento_pre_postagem(loc, esperado):
    from app.services.logistica_track import evento_pre_postagem

    assert evento_pre_postagem(loc) is esperado
