"""logistica_rules.tiktok_localizacao_pt — a `description` (inglês) do último
evento de tracking do TikTok vira texto PT na coluna Localização (Vinicius,
21/09/2026). Cobre TODOS os textos vistos em produção (2000 linhas, 30
distintos) + os do histórico dos pedidos, o fallback por `action_code`, o
texto desconhecido e a normalização (espaços duplos, pontuação final)."""

from __future__ import annotations

import pytest

from app.services import logistica_rules, logistica_tiktok

# Últimos eventos reais (produção, 21/09/2026).
ULTIMOS_EVENTOS = [
    ("Package has been delivered!", "Entregue"),
    ("Order packed and ready for pickup.", "Embalado, aguardando coleta"),
    ("Order packed and ready for dropoff at carrier's facility.", "Embalado, aguardando postagem"),
    ("Package dropped off at Piracicaba.", "Postado em Piracicaba"),
    ("Departed the Barueri shipping partner's facility.", "Saiu do hub de Barueri"),
    ("Package lost in transit.", "Extraviado em trânsito"),
    ("Package dropped off at Americana.", "Postado em Americana"),
    ("Package dropped off at São Bernardo do Campo.", "Postado em São Bernardo do Campo"),
    ("Departed the Guarulhos shipping partner's facility.", "Saiu do hub de Guarulhos"),
    ("Arrived at the carrier's facility.", "Chegou na transportadora"),
    ("Departed the Nova Odessa shipping partner's facility.", "Saiu do hub de Nova Odessa"),
    ("Arrived at the Sobral shipping partner's facility.", "Chegou no hub de Sobral"),
    (
        "Arrived at the Feira de Santana shipping partner's facility.",
        "Chegou no hub de Feira de Santana",
    ),
    ("Package dropped off at Limeira.", "Postado em Limeira"),
    ("Package dropped off with carrier.", "Postado na transportadora"),
    ("Arrived at the Manaus shipping partner's facility.", "Chegou no hub de Manaus"),
    (
        "Your package has been delayed while in transit. Please check for updates.",
        "Atrasado em trânsito",
    ),
    ("Arrived at the Juiz de Fora shipping partner's facility.", "Chegou no hub de Juiz de Fora"),
    ("Arrived at the Manaus destination facility.", "Chegou na base de entrega de Manaus"),
    ("Arrived at the Piracicaba destination facility.", "Chegou na base de entrega de Piracicaba"),
    (
        "Departed the Barueri carrier's facility and in transit to Brasília.",
        "Saiu de Barueri, a caminho de Brasília",
    ),
    (
        "Departed the Barueri carrier's facility and in transit to Manaus.",
        "Saiu de Barueri, a caminho de Manaus",
    ),
    (
        "Departed the Feira de Santana shipping partner's facility.",
        "Saiu do hub de Feira de Santana",
    ),
    ("Departed the Passo Fundo shipping partner's facility.", "Saiu do hub de Passo Fundo"),
    ("Order placed.", "Pedido realizado"),
    ("Out for delivery.", "Saiu para entrega"),
    ("Package damaged in transit.", "Danificado em trânsito"),
    (
        "Package picked up and currently at São Bernardo do Campo.",
        "Coletado em São Bernardo do Campo",
    ),
    (
        "Package was unable to be delivered due to unavailable recipient.  Delivery will be "
        "rescheduled, so please check for updates.",
        "Falha na entrega: destinatário ausente — nova tentativa",
    ),
    ("Arrived at the Americana carrier's facility.", "Chegou na transportadora em Americana"),
]

# Textos vistos no histórico dos pedidos (podem virar último evento).
HISTORICO = [
    ("Processed at the carrier's facility.", "Processado na transportadora"),
    ("Out for redelivery.", "Saiu para nova tentativa de entrega"),
    ("Out for final redelivery.", "Saiu para última tentativa de entrega"),
    (
        "Package was unable to be delivered due to it has an invalid shipping address.  "
        "Delivery will be rescheduled, so please check for updates.",
        "Falha na entrega: endereço inválido — nova tentativa",
    ),
    (
        "Package was unable to be delivered. Delivery will be rescheduled, so please check "
        "for updates.",
        "Falha na entrega — nova tentativa",
    ),
    (
        "Package was unable to be delivered due to unavailable recipient.",
        "Falha na entrega: destinatário ausente",
    ),
    (
        "Package was unable to be picked up. A redelivery will be scheduled. Please check "
        "for updates.",
        "Falha na coleta — será reagendada",
    ),
    (
        "Package was unable be picked up because it is still being prepared. For help, "
        "contact your shipping carrier.",
        "Falha na coleta: pacote ainda em preparação",
    ),
    (
        "Departed the Parnaíba destination facility and in transit to Luzilândia.",
        "Saiu da base de entrega de Parnaíba, a caminho de Luzilândia",
    ),
    ("Departed the carrier's facility.", "Saiu da transportadora"),
    ("Departed the Barueri carrier's facility.", "Saiu da transportadora em Barueri"),
    (
        "Departed the Barueri shipping partner's facility and in transit to Manaus.",
        "Saiu do hub de Barueri, a caminho de Manaus",
    ),
]


@pytest.mark.parametrize(("en", "pt"), ULTIMOS_EVENTOS + HISTORICO)
def test_traduz_todos_os_textos_reais(en, pt):
    assert logistica_rules.tiktok_localizacao_pt(en) == pt


@pytest.mark.parametrize(("en", "pt"), ULTIMOS_EVENTOS + HISTORICO)
def test_traducao_nunca_deixa_ingles_nem_ponto_final(en, pt):
    out = logistica_rules.tiktok_localizacao_pt(en)
    assert out == pt
    assert not out.endswith((".", "!"))
    low = out.lower()
    for palavra in ("package", "facility", "delivered", "arrived", "departed", "carrier"):
        assert palavra not in low


def test_motivo_de_falha_desconhecido_fica_em_ingles_dentro_do_rotulo():
    # Só a família é conhecida; o motivo é raro e sai como veio.
    en = (
        "Package was unable to be delivered due to business closed. "
        "Delivery will be rescheduled, so please check for updates."
    )
    assert logistica_rules.tiktok_localizacao_pt(en) == (
        "Falha na entrega: business closed — nova tentativa"
    )
    assert logistica_rules.tiktok_localizacao_pt(
        "Package was unable to be delivered due to business closed."
    ) == "Falha na entrega: business closed"


def test_normaliza_espacos_pontuacao_e_caixa():
    f = logistica_rules.tiktok_localizacao_pt
    assert f("  package HAS been   delivered!!  ") == "Entregue"
    assert f("Arrived at the  Manaus  shipping partner's facility") == "Chegou no hub de Manaus"
    assert f("Out for delivery") == "Saiu para entrega"
    # Apóstrofo tipográfico também casa.
    assert f("Arrived at the carrier’s facility.") == "Chegou na transportadora"


def test_desconhecido_sem_codigo_devolve_o_original():
    f = logistica_rules.tiktok_localizacao_pt
    assert f("  Something brand new.  ") == "Something brand new."


def test_desconhecido_com_codigo_conhecido_cai_na_familia():
    f = logistica_rules.tiktok_localizacao_pt
    assert f("Something brand new.", 50101) == "Entregue"
    assert f("Something brand new.", 31001) == "Chegou no hub"
    assert f("Something brand new.", 40601) == "Falha na entrega"
    # Código desconhecido → texto original.
    assert f("Something brand new.", 777) == "Something brand new."


def test_regex_vence_o_codigo():
    # O código NÃO é unívoco (20101 sai como pickup E dropoff): o texto manda.
    assert logistica_rules.tiktok_localizacao_pt(
        "Order packed and ready for dropoff at carrier's facility.", 20101
    ) == "Embalado, aguardando postagem"


def test_vazio():
    f = logistica_rules.tiktok_localizacao_pt
    assert f(None) is None
    assert f("") is None
    assert f("   ") is None
    # Evento sem texto mas com código: o rótulo da família ainda serve.
    assert f(None, 50101) == "Entregue"


def test_codigos_da_familia_cobrem_o_observado():
    for code in (
        10101, 20101, 20201, 20301, 30901, 31001, 31101, 31301, 31401, 32101, 32401, 32601,
        32701, 36201, 39901, 40101, 40201, 40501, 40601, 40801, 41001, 41101, 50101, 50102,
        60101, 90101, 100101, 3010001,
    ):
        assert code in logistica_rules.TIKTOK_ACTION_CODE_PT


def test_tiktok_localizacao_escolhe_o_ultimo_evento_e_traduz():
    track = {
        "tracking": [
            {"action_code": 32701, "description": "Processed at the carrier's facility.",
             "update_time_millis": 100},
            {"action_code": "50101", "description": "Package has been delivered!",
             "update_time_millis": 300},
            {"action_code": 40501, "description": "Out for delivery.",
             "update_time_millis": 200},
        ]
    }
    assert logistica_tiktok._tiktok_localizacao(track) == "Entregue"
    # Texto novo + código (string) conhecido → família; código ilegível → original.
    track = {"tracking": [{"action_code": "50102", "description": "Delivered to locker."}]}
    assert logistica_tiktok._tiktok_localizacao(track) == "Entregue"
    track = {"tracking": [{"action_code": "x", "description": "Delivered to locker."}]}
    assert logistica_tiktok._tiktok_localizacao(track) == "Delivered to locker."
    assert logistica_tiktok._tiktok_localizacao({"tracking": []}) is None


def test_divergencia_reconhece_os_rotulos_em_pt():
    f = logistica_rules.detectar_divergencia_tiktok
    # Entregue = igualdade, não substring.
    assert f({"order_status": "CANCELLED"}, "Entregue") is not None
    assert f({"order_status": "CANCELLED"}, "Falha na entrega: destinatário ausente") is None
    assert f({"order_status": "CANCELLED"}, "Saiu para entrega") is None
    # Problema = prefixo dos rótulos PT.
    for loc in (
        "Extraviado em trânsito",
        "Danificado em trânsito",
        "Falha na entrega — nova tentativa",
        "Devolvido → loja",
        "Devolução a caminho",
    ):
        assert f({"order_status": "DELIVERED"}, loc) is not None, loc
    for loc in ("Entregue", "Saiu para entrega", "Chegou no hub de Manaus", "Postado em Limeira"):
        assert f({"order_status": "COMPLETED"}, loc) is None, loc


def test_divergencia_mantem_compatibilidade_com_o_ingles_ate_a_migracao():
    f = logistica_rules.detectar_divergencia_tiktok
    assert f({"order_status": "CANCELLED"}, "Package has been delivered!") is not None
    # "unable to be delivered" NÃO é entrega.
    assert f(
        {"order_status": "CANCELLED"},
        "Package was unable to be delivered due to unavailable recipient.",
    ) is None
    assert f({"order_status": "COMPLETED"}, "Package lost in transit.") is not None
