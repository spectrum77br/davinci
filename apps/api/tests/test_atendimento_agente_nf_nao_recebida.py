"""Barbosa ao vivo (22/10/2026 08:33, 8e335ebf): "Não recebi a nota fiscal" com o
pedido entregue não põe mais o "Abrir chamado (entrega)" — a conversa segue para
pessoa pelo "Enviar o arquivo da NF" (gatilho 9)."""

import pytest

from app.services.atendimento import constantes
from app.services.atendimento.agente import acao_pessoa

NF = constantes.ACAO_ENVIAR_ARQUIVO_NF


@pytest.mark.parametrize(
    "plano",
    [
        "nao recebi a nota fiscal",
        "ainda nao recebi a nf",
        "nao veio a nota",
        "n recebi o xml",
        "boa tarde nao recebi minha nota fiscal do pedido",
    ],
)
def test_so_a_nf_com_o_gatilho_9_tira_o_chamado(plano):
    assert acao_pessoa.so_a_nf_nao_chegou(plano, [NF])


@pytest.mark.parametrize(
    "plano",
    [
        # Sem o gatilho 9 a regra não mexe em nada.
        "nao recebi a nota fiscal",
    ],
)
def test_sem_o_gatilho_9_nada_muda(plano):
    assert not acao_pessoa.so_a_nf_nao_chegou(plano, [])


@pytest.mark.parametrize(
    "plano",
    [
        # O pedido também não chegou: o chamado de entrega fica.
        "nao recebi a nota fiscal e nao recebi o pedido",
        "nao recebi a nota e o produto nao chegou",
        "nao recebi o pedido",
        "consta entregue mas nao recebi, nem a nota",
        # Só o pedido, sem falar da nota.
        "nao chegou ate agora",
    ],
)
def test_o_pedido_que_nao_chegou_segue_com_o_chamado(plano):
    assert not acao_pessoa.so_a_nf_nao_chegou(plano, [NF])
