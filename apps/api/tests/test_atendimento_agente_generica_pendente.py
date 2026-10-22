"""viz-responde-03 (calibração v8 com a chave, 22/10/2026): o "como podemos ajudar?"
de cortesia com a pergunta deixada só com a resposta genérica da loja não sai sozinho."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.atendimento import constantes
from app.services.atendimento.agente import acao_pessoa, checagens

AGORA = datetime(2026, 10, 21, 20, 32, tzinfo=UTC)


def _m(autor, texto, minutos, **extra):
    quando = AGORA - timedelta(minutes=minutos)
    return SimpleNamespace(
        id=None,
        autor=autor,
        texto=texto,
        tipo=extra.get("tipo", "texto"),
        payload=extra.get("payload", {}),
        origem=extra.get("origem"),
        status="enviada",
        enviada_em=quando,
        created_at=quando,
        anexos=[],
    )


C, L = constantes.AUTOR_CLIENTE, constantes.AUTOR_LOJA


def _conversa(pergunta, loja, ultima):
    return [
        _m(C, "", 46, tipo="produto", payload={"message_type": "item"}),
        _m(C, pergunta, 46),
        _m(L, loja, 45),
        _m(C, ultima, 2),
    ]


@pytest.mark.parametrize(
    ("pergunta", "loja", "ultima"),
    [
        (
            "o A17 Pro Max laranja vai voltar ao estoque?",
            "Olá! Em que podemos ajudar sobre esse produto ou pedido?",
            "6",
        ),
        ("o A17 Pro Max laranja vai voltar ao estoque?", "Oi! Qual é a sua dúvida?", "oi"),
        ("queria saber se o laranja volta", "Olá! Em que podemos ajudar?", "Boa tarde"),
        ("Comprando hj q dia Chega em BH", "Olá! Como podemos ajudar?", "6"),
    ],
)
@pytest.mark.parametrize("assunto", ["saudacao_ou_vaga", "pede_atendente", "agradecimento"])
def test_cortesia_com_pergunta_deixada_na_generica_e_suave(pergunta, loja, ultima, assunto):
    recentes = _conversa(pergunta, loja, ultima)
    pend = checagens.conferir_generica_com_pendencia(assunto, recentes, recentes[-1])
    assert [(p.codigo, p.so_auto, p.bloqueia) for p in pend] == [
        (checagens.GENERICA_COM_PENDENCIA, True, False)
    ]


@pytest.mark.parametrize(
    ("pergunta", "loja", "ultima", "assunto"),
    [
        # o assunto que responde: a checagem não é dele
        (
            "o laranja vai voltar ao estoque?",
            "Olá! Em que podemos ajudar?",
            "6",
            "estoque_variacao",
        ),
        # resposta de verdade da loja antes do "6"
        (
            "esse vem com capinha?",
            "Vem sim, a capa e a película acompanham o aparelho.",
            "6",
            "pede_atendente",
        ),
        # só cumprimento, figurinha, "é robô?" e o pedido de atendente deixados
        ("Boa tarde", "Olá! Em que podemos ajudar?", "6", "saudacao_ou_vaga"),
        (
            "[Figurinha]",
            "Olá! Como podemos ajudar com seu pedido?",
            "Boa tarde",
            "saudacao_ou_vaga",
        ),
        ("vc é robô?", "Olá! Em que podemos ajudar?", "6", "pede_atendente"),
        ("Olá, alguém pode me ajudar?", "Olá! Em que podemos ajudar?", "6", "saudacao_ou_vaga"),
        # a recusa à pergunta genérica
        (
            "1",
            "Bom dia! Ficou alguma dúvida em que eu possa te ajudar?",
            "Não obrigado",
            "agradecimento",
        ),
    ],
)
def test_sem_pendencia_deixada_nao_segura(pergunta, loja, ultima, assunto):
    recentes = _conversa(pergunta, loja, ultima)
    assert checagens.conferir_generica_com_pendencia(assunto, recentes, recentes[-1]) == []


def test_sem_resposta_real_da_loja_nao_segura():
    # boa-aj15-426b9b20: só automáticas da loja (menu), cumprimentos e a opção 6.
    menu = (
        "Olá, por favor selecione sua dúvida e logo um dos nossos consultores irá atendê-lo! "
        "1 - Previsão de entrega / envio 2 - Nota fiscal 3 - Encerramento da compra "
        "4 - Troca de endereço 5 - Garantia 6 - Falar com Atendente"
    )
    recentes = [
        _m(C, "", 320, tipo="produto", payload={"message_type": "item"}),
        _m(C, "Olá Boa tarde", 320),
        _m(L, menu, 319),
        _m(C, "6", 319),
        _m(C, "Boa tarde", 236),
        _m(C, "Boa noite", 3),
    ]
    assert not acao_pessoa.fala_deixada_com_generica(recentes, recentes[-1])
    assert (
        checagens.conferir_generica_com_pendencia("saudacao_ou_vaga", recentes, recentes[-1]) == []
    )
