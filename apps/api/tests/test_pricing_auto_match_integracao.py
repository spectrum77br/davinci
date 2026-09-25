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


def test_palavra_generica_nao_liga_em_outra_loja():
    victor, lucas = _integ("victor mei"), _integ("lucas mei")
    assert _escolher_integracao("victor mei premium", None, [lucas, victor]) is victor
    # "lucas mei" sem a integração dele: não pode cair na "victor mei" pelo "mei".
    assert _escolher_integracao("lucas mei classico", None, [victor]) is None
    assert _escolher_integracao("mei premium", None, [lucas, victor]) is None


def test_nome_da_integracao_contido_no_nome_da_conta():
    kfa, kfa2 = _integ("kfa"), _integ("kfa2")
    assert _escolher_integracao("kfa amazon", None, [kfa2, kfa]) is kfa


def test_pedaco_do_nome_nao_casa_mais():
    assert _escolher_integracao("kfa premium", None, [_integ("kfa2"), _integ("inova")]) is None


def test_unica_integracao_da_plataforma_sem_nome_nao_liga():
    # Antes: plataforma com uma integração só ligava qualquer conta nela.
    so = _integ("KFA Amazon")
    assert _escolher_integracao("poofy", None, [so]) is None
    assert _escolher_integracao("amazon lucas", None, [so]) is None
    assert _escolher_integracao("kfa amazon", None, [so]) is so
