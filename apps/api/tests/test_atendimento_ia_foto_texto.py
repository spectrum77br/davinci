# ruff: noqa: F401, F811 — fixtures compartilhadas com a suíte da IA
"""Foto + texto continuam no mesmo turno quando chega o menu do Duoke.

Modelo falso, sem chamadas à plataforma. O gatilho pode ser a foto; o
texto da mesma rajada precisa chegar ao modelo e à triagem da Caixa Humano.
"""

from datetime import timedelta

import pytest

from app.services.atendimento import humano, ia
from app.services.atendimento.constantes import (
    HUMANO_SO_ANEXO,
    MSG_FALHOU,
    ORIGEM_NOTA,
    RASCUNHO_BLOQUEADO,
    RASCUNHO_PENDENTE,
)
from tests.test_atendimento_humano import _cli, _conv, _ctx, _loja
from tests.test_atendimento_ia import (
    _conversa,
    _msg,
    _saida,
    ia_ligada,
    modelo,
)

MENU_DUOKE = "Olá, por favor selecione sua dúvida e logo um dos nossos consultores irá atendê-lo!"
PERGUNTA = "Gostaria de saber mais sobre a mala."


@pytest.mark.parametrize(
    ("texto_loja", "origem"),
    [(MENU_DUOKE, "externo"), (MENU_DUOKE, "davinci_auto")],
)
async def test_foto_com_texto_e_menu_chega_ao_modelo_com_o_mesmo_gatilho(
    db, make_user, ia_ligada, modelo, texto_loja, origem
):
    conversa = await _conversa(db, make_user, pedido=None)
    await _msg(db, conversa, PERGUNTA, ha=timedelta(minutes=6))
    foto = await _msg(db, conversa, None, tipo="imagem", ha=timedelta(minutes=5))
    await _msg(db, conversa, texto_loja, autor="loja", origem=origem, ha=timedelta(minutes=4))
    modelo.saida = _saida("Olá! Estou à disposição.", categoria="duvida_produto")

    rascunho = await ia.gerar_rascunho(db, conversa)

    assert modelo.chamadas and PERGUNTA in modelo.usuario
    assert rascunho.status == RASCUNHO_PENDENTE
    assert "só foto" not in (rascunho.motivo or "")
    assert rascunho.mensagem_gatilho_id == foto.id


async def test_so_foto_seguida_do_menu_continua_para_pessoa(db, make_user, ia_ligada, modelo):
    conversa = await _conversa(db, make_user, pedido=None)
    foto = await _msg(db, conversa, None, tipo="imagem", ha=timedelta(minutes=5))
    await _msg(db, conversa, MENU_DUOKE, autor="loja", ha=timedelta(minutes=4))

    rascunho = await ia.gerar_rascunho(db, conversa)

    assert modelo.chamadas == []
    assert rascunho.status == RASCUNHO_BLOQUEADO and rascunho.precisa_humano
    assert "só foto" in rascunho.motivo
    assert rascunho.mensagem_gatilho_id == foto.id


@pytest.mark.parametrize("origem", ["externo", "davinci_humano", "davinci_ia"])
async def test_texto_ja_respondido_nao_mascara_foto_no_turno_novo(
    db, make_user, ia_ligada, modelo, origem
):
    conversa = await _conversa(db, make_user, pedido=None)
    await _msg(db, conversa, PERGUNTA, ha=timedelta(minutes=10))
    await _msg(
        db,
        conversa,
        "Olá! Estou à disposição.",
        autor="loja",
        origem=origem,
        ha=timedelta(minutes=8),
    )
    await _msg(db, conversa, None, tipo="imagem", ha=timedelta(minutes=5))
    await _msg(db, conversa, MENU_DUOKE, autor="loja", ha=timedelta(minutes=4))

    rascunho = await ia.gerar_rascunho(db, conversa)

    assert modelo.chamadas == []
    assert rascunho.status == RASCUNHO_BLOQUEADO and rascunho.precisa_humano
    assert "só foto" in rascunho.motivo


@pytest.mark.parametrize("com_texto", [True, False])
def test_caixa_humano_usa_o_mesmo_turno_da_ia_depois_do_menu(com_texto):
    foto = _cli(tipo="imagem")
    mensagens = [*([_cli(PERGUNTA)] if com_texto else []), foto, _loja(MENU_DUOKE)]

    triagem = humano.triar(_conv(), mensagens, _ctx(), so_humano=(), gatilho=foto)

    assert (HUMANO_SO_ANEXO in triagem.motivos) is not com_texto


def test_caixa_humano_so_foto_depois_da_resposta_real_continua_para_pessoa():
    foto = _cli(tipo="imagem")
    mensagens = [
        _cli(PERGUNTA),
        _loja("Olá! Estou à disposição."),
        foto,
        _loja(MENU_DUOKE),
    ]

    triagem = humano.triar(_conv(), mensagens, _ctx(), so_humano=(), gatilho=foto)

    assert HUMANO_SO_ANEXO in triagem.motivos


@pytest.mark.parametrize(
    ("origem", "status"),
    [("davinci_auto", "enviada"), (ORIGEM_NOTA, "enviada"), ("externo", MSG_FALHOU)],
)
def test_automacao_nota_e_envio_falho_nao_separam_o_texto_da_foto(origem, status):
    texto, foto = _cli(PERGUNTA), _cli(tipo="imagem")
    mensagem = _loja("Mensagem da loja.")
    mensagem.origem, mensagem.status = origem, status

    assert ia.rajada_do_cliente([texto, foto, mensagem], foto) == [texto, foto]
