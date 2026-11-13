"""O "segurar envio" não vale para pedido já cancelado (o eval de 13/11/2026).

"Cancela" num chat da Shopee em que o único pedido era o cartão com status "Cancelado" (o
pedido fora do sistema): o código não sabia o estado, ficou no lado seguro e pediu
"segurar envio" nos dois modelos. Cancelado não tem envio a segurar.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.atendimento import constantes
from app.services.atendimento.agente import acao_pessoa


def _cartao(status: str | None) -> SimpleNamespace:
    anexo = {"tipo": "pedido", "pedido": "#F2Q", **({"status": status} if status else {})}
    return SimpleNamespace(anexos=[anexo], payload={})


def _conversa(**campos) -> SimpleNamespace:
    return SimpleNamespace(canal="chat", pedido_marketplace=None, **campos)


def test_so_o_cartao_cancelado_nao_tem_o_que_segurar():
    conversa = _conversa()
    assert (
        acao_pessoa.pode_nao_ter_saido(None, conversa, ctx={}, recentes=[_cartao("Cancelado")])
        is False
    )
    dois = [_cartao("Cancelado"), _cartao("CANCELLED")]
    assert acao_pessoa.pode_nao_ter_saido(None, conversa, ctx={}, recentes=dois) is False


def test_cartao_aberto_ou_sem_status_continua_no_lado_seguro():
    conversa = _conversa()
    aberto = [_cartao("Aguardando envio")]
    assert acao_pessoa.pode_nao_ter_saido(None, conversa, ctx={}, recentes=aberto) is True
    misto = [_cartao("Cancelado"), _cartao("A enviar")]
    assert acao_pessoa.pode_nao_ter_saido(None, conversa, ctx={}, recentes=misto) is True
    sem_status = [_cartao(None)]
    assert acao_pessoa.pode_nao_ter_saido(None, conversa, ctx={}, recentes=sem_status) is True
    cru = SimpleNamespace(anexos=[], payload={"message_type": constantes.TIPO_SHOPEE_CARTAO_PEDIDO})
    assert acao_pessoa.pode_nao_ter_saido(None, conversa, ctx={}, recentes=[cru]) is True


def test_pedido_no_contexto_ou_na_conversa_segue_a_regra_de_antes():
    cancelado = [_cartao("Cancelado")]
    assert (
        acao_pessoa.pode_nao_ter_saido(
            None, _conversa(), ctx={"pedido": {"numero": "1"}}, recentes=cancelado
        )
        is True
    )
    com_pedido = SimpleNamespace(canal="chat", pedido_marketplace="2511ABC")
    assert acao_pessoa.pode_nao_ter_saido(None, com_pedido, ctx={}, recentes=cancelado) is True
    # O estado conhecido manda (o espelho do Bling/a logística), como sempre.
    assert (
        acao_pessoa.pode_nao_ter_saido(
            acao_pessoa.POSTADO_NAO, _conversa(), ctx={}, recentes=cancelado
        )
        is True
    )
