"""Regra de sábado da etiqueta (migration 0223) — validadores puros.

Correios segue contínuo no sábado; agência emite uma vez no horário e só nos
estoques marcados.
"""

import pytest

from app.schemas.pricing import (
    StoreInfoPatch,
    normaliza_hora_sabado,
    normaliza_tags_estoque,
)


def test_hora_sabado_aceita_um_horario():
    assert normaliza_hora_sabado("8h") == "08:00"
    assert normaliza_hora_sabado("") is None
    assert normaliza_hora_sabado(None) is None


def test_hora_sabado_recusa_mais_de_um():
    with pytest.raises(ValueError):
        normaliza_hora_sabado("08:00, 10:00")


def test_tags_ordena_e_normaliza():
    assert normaliza_tags_estoque("SP, pi , ra") == "pi, ra, sp"
    assert normaliza_tags_estoque("") is None


def test_tags_recusa_slug_desconhecido():
    with pytest.raises(ValueError):
        normaliza_tags_estoque("pi, xx")


def test_patch_valida_os_dois_campos():
    patch = StoreInfoPatch(etiqueta_sabado_horario="0800", etiqueta_sabado_tags="ra,pi")
    assert patch.etiqueta_sabado_horario == "08:00"
    assert patch.etiqueta_sabado_tags == "pi, ra"
