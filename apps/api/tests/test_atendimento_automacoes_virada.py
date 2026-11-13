"""As automáticas na VIRADA do agente (13/11/2026, o Eduardo).

Todas as lojas vão para o automático do agente. As de resposta (menu, opções, "aguarde") e o
pedido recebido e a pós-conclusão ACABAM (no automático elas não saem); as de evento que
chamam o cliente FICAM: não pago com cupom ("carrinho"), entregue, convite para seguir a
loja, "ficou alguma dúvida?" e o chat da avaliação. O brinde e o lembrete já saíam.
A chave `atendimento_automacoes_eventos_no_auto` liga junto com a virada.
"""

from __future__ import annotations

import pytest

from app.services.atendimento import automacoes_catalogo as cat

FICAM = {"nao_pago", "entregue", "convite", "duvida_1", "duvida_2"}
ACABAM = {"menu", "opcao", "aguarde", "pedido_recebido", "pos"}


def _autos(tipos: set[str]) -> list[cat.Automacao]:
    return [a for a in cat.CATALOGO.values() if a.tipo in tipos]


def test_o_conjunto_do_codigo_e_o_combinado():
    assert cat.TIPOS_SEGUEM_NO_AUTOMATICO == FICAM
    assert not cat.TIPOS_SEGUEM_NO_AUTOMATICO & ACABAM
    assert {a.tipo for a in cat.CATALOGO.values()} >= FICAM | ACABAM


@pytest.mark.parametrize("aut", _autos(FICAM), ids=lambda a: a.codigo)
def test_as_que_ficam_saem_no_automatico_com_a_chave(aut):
    sem = cat.decidir(aut, {"canal_auto": True})
    com = cat.decidir(aut, {"canal_auto": True, "eventos_no_auto": True})
    assert com != "ia_no_automatico"
    # Sem a chave, como hoje: param no automático (a não ser o que já não é desta regra).
    assert sem in ("ia_no_automatico", "sem_texto")


@pytest.mark.parametrize("aut", _autos(ACABAM), ids=lambda a: a.codigo)
def test_as_que_acabam_continuam_parando_no_automatico(aut):
    assert cat.decidir(aut, {"canal_auto": True, "eventos_no_auto": True}) in (
        "ia_no_automatico",
        "sem_texto",
    )


@pytest.mark.parametrize("tipo", ["nao_pago_brinde", "nao_pago_lembrete"])
def test_o_brinde_e_o_lembrete_seguem_como_ja_eram(tipo):
    for aut in _autos({tipo}):
        for chave in (False, True):
            assert cat.decidir(aut, {"canal_auto": True, "eventos_no_auto": chave}) != (
                "ia_no_automatico"
            )


def test_o_chat_da_avaliacao_sai_no_automatico_com_a_chave():
    for aut in _autos({"avaliacao_boa", "avaliacao_ruim"}):
        assert cat.chat_segurado_pela_ia(aut, {"canal_auto": True}) == "ia_no_automatico"
        assert cat.chat_segurado_pela_ia(aut, {"canal_auto": True, "eventos_no_auto": True}) is None
