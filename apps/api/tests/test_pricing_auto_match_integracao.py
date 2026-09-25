"""Ligação automática conta de preço → integração (25/09/2026).

"kfa classico" e "kfa premium" tinham sido ligadas à integração da kfa2
porque o casamento era por pedaço do nome ("kfa" in "kfa2"). A conta sumia
da Tabela de Preços da equipe da KFA e os preços lidos eram os da kfa2.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.routers.pricing import _escolher_integracao


def _integ(nome: str):
    return SimpleNamespace(id=uuid.uuid4(), name=nome)


def test_nao_confunde_kfa_com_kfa2():
    kfa2, kfa = _integ("kfa2"), _integ("kfa")
    # kfa2 primeiro na lista: era ela que ganhava antes.
    assert _escolher_integracao("kfa premium", None, [kfa2, kfa]) is kfa
    assert _escolher_integracao("kfa classico", None, [kfa2, kfa]) is kfa
    assert _escolher_integracao("kfa2 Classico", None, [kfa, kfa2]) is kfa2


def test_integracao_da_loja_ligada_manda():
    kfa2, kfa = _integ("kfa2"), _integ("kfa")
    assert _escolher_integracao("qualquer nome", kfa.id, [kfa2, kfa]) is kfa


def test_espacos_e_maiusculas_no_nome_da_integracao():
    velasco, outra = _integ(" Velasco"), _integ("inova")
    assert _escolher_integracao("velasco premium", None, [outra, velasco]) is velasco


def test_palavra_inteira_unica_e_empate_nao_chuta():
    victor, lucas = _integ("victor mei"), _integ("lucas mei")
    # "mei" está nas duas; "victor" só numa → decide pela palavra única.
    assert _escolher_integracao("victor premium", None, [lucas, victor]) is victor
    # Só "mei" em comum com as duas: empate → não liga.
    assert _escolher_integracao("mei premium", None, [lucas, victor]) is None


def test_pedaco_do_nome_nao_casa_mais():
    assert _escolher_integracao("kfa premium", None, [_integ("kfa2"), _integ("inova")]) is None


def test_unica_integracao_da_plataforma_continua_valendo():
    so = _integ("conta unica")
    assert _escolher_integracao("outra coisa premium", None, [so]) is so
