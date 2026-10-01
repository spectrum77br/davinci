"""A resposta automática do Duoke não tira a conversa da fila (01/10/2026).

O robô do Duoke responde sozinho pela mesma API da pessoa ("selecione sua
dúvida", "sua mensagem foi recebida"): chega como mensagem DA LOJA. A Caixa
tratava isso como resposta, e a Shopee ATV mostrava 2 conversas esperando
quando eram 7.
"""

from datetime import UTC, datetime, timedelta

from app.models import AtendimentoConversa, AtendimentoMensagem
from app.services.atendimento import gravar
from app.services.atendimento.constantes import e_resposta_automatica

T0 = datetime(2026, 10, 1, 7, 30, tzinfo=UTC)


def _msg(autor: str, texto: str, minutos: int) -> AtendimentoMensagem:
    return AtendimentoMensagem(
        autor=autor,
        origem="cliente" if autor == "cliente" else "externo",
        tipo="texto",
        texto=texto,
        enviada_em=T0 + timedelta(minutes=minutos),
        status="recebida",
        payload={},
    )


def test_reconhece_os_modelos_do_duoke():
    assert e_resposta_automatica(
        "Olá, por favor selecione sua dúvida e logo um atendente irá te responder"
    )
    assert e_resposta_automatica("OLA,  a sua mensagem foi RECEBIDA. Há mais mensagens…")
    assert e_resposta_automatica("Descreva sua dúvida que assim que um atendente estiver livre")
    # Campanhas automáticas que no TikTok chegam como mensagem da loja.
    assert e_resposta_automatica("Oi! Recebemos seu pedido e já estamos preparando pra envio.")
    assert e_resposta_automatica("Ficou alguma dúvida sobre o produto? Estou aqui pra te ajudar")
    assert e_resposta_automatica("Oi! Seu produto ainda está no carrinho 🛒 Finalize agora")
    assert e_resposta_automatica("Oi! Tudo bem? 😊 Confirmamos a entrega do seu pedido! Por se")
    # Resposta de pessoa (mesmo as prontas do Duoke) continua contando.
    assert not e_resposta_automatica("A entrega é feita pela Shopee, não temos acesso")
    assert not e_resposta_automatica("bom dia tudo bem ?")
    assert not e_resposta_automatica("Olá! Seu pedido já foi enviado")
    assert not e_resposta_automatica(None)
    assert not e_resposta_automatica("")


def test_automatica_nao_tira_da_fila_e_a_de_pessoa_tira():
    conversa = AtendimentoConversa(plataforma="shopee", canal="chat", externo_id="c1")
    gravar.recalcular(
        conversa,
        [
            _msg("cliente", "a garantia como fica?", 0),
            _msg("loja", "Olá, a sua mensagem foi recebida. Há mais mensagens", 10),
        ],
    )
    assert conversa.aguardando_resposta is True
    # A lista continua mostrando a última mensagem (a automática).
    assert conversa.ultima_autor == "loja"

    gravar.recalcular(conversa, [_msg("loja", "Pode ficar tranquila, a garantia é de 90 dias", 20)])
    assert conversa.aguardando_resposta is False
