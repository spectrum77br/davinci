"""classify_sku_tag + sql_clause_for_tag.

Cobertura:
  - _SKU_TAG_OVERRIDES (SKUs hand-curated, precedência máxima)
  - Regra 0 (z*): z*.mala/eletro mantêm operador; z* SEM sufixo → us;
                  z* COM sufixo regional cai na regra 1 (sufixo vence)
  - Regras 1-4 originais (sufixo / fake / mala / eletro)
"""
from __future__ import annotations

import pytest
from sqlalchemy import Column, String

from app.services.sku_tags import classify_sku_tag, sql_clause_for_tag

_col = Column("sku", String)


# ── classify_sku_tag ──────────────────────────────────────────────


@pytest.mark.parametrize("sku,expected", [
    # Overrides (precedência máxima — vence sufixo, prefixo, regra do z)
    ("dg001.us", "sa"),
    ("dg011.us", "sa"),
    ("dg017.us", "sa"),
    ("dg023.us", "sa"),
    ("dg061.us", "sa"),
    ("zr010.us", "sa"),    # override vence regra 0 + sufixo
    ("b000.us", "mala"),
    ("z0097.us", "mala"),  # override vence regra 0
    # Acessórios de mala a* (sem sufixo) → mala
    ("a006", "mala"),
    ("a015", "mala"),
    ("a073", "mala"),
    ("a074", "mala"),
    ("a075", "mala"),
    ("a076", "mala"),
    ("A015", "mala"),   # normalização lowercase
    ("a001", None),     # a* fora da lista segue sem tag
    # Mochilas bp* → mala (override; não casam ^b[0-9])
    ("bp001", "mala"),
    ("bp002", "mala"),
    ("bp003", "mala"),
    # Regra 0: z* SEM sufixo → us
    ("z0099", "us"),
    ("z0101", "us"),
    # Carve-outs da regra 0
    ("z0094.mala", "mala"),
    ("z0095.mala", "mala"),
    ("z0096.mala", "mala"),
    ("z0098.mala", "mala"),
    ("z0010.eletro", "eletro"),
    # Revert: z* COM sufixo regional volta a cair na regra 1 (sufixo)
    ("z0100.ra", "ra"),
    ("zx024.ra", "ra"),
    ("za100.pi", "pi"),
    ("zdg005.pi", "pi"),
    ("zdg016.pi", "pi"),
    # Sufixo regular sem z continua valendo
    ("a001.pi", "pi"),
    ("b045.ra", "ra"),
    ("u200.sa", "sa"),
    ("a001.us", "us"),
    # Demais regras (sem mudança)
    ("b001", "mala"),
    ("b1234", "mala"),
    ("u001", "eletro"),
    ("u300", "eletro"),
    ("fake.x", "fake"),
    ("bp999", None),    # bp fora da lista não casa ^b[0-9] nem override
    ("", None),
    (None, None),
])
def test_classify_sku_tag(sku, expected):
    assert classify_sku_tag(sku) == expected


def test_classify_sku_tag_kit_z_sem_sufixo_vai_pra_us():
    """Kit com um componente z SEM sufixo → us (regra 0 dispara)."""
    assert classify_sku_tag("a001+z0099") == "us"


def test_classify_sku_tag_kit_carve_out_mala_vence():
    """z0094.mala como kit-component → carve-out aplica."""
    assert classify_sku_tag("a001+z0094.mala") == "mala"


def test_classify_sku_tag_kit_z_com_sufixo_regional_cai_no_sufixo():
    """z*.pi em kit cai na regra 1 — vai pra pi (revert)."""
    # b001 sozinho daria 'mala', mas a regra 1 (sufixo) pega antes.
    assert classify_sku_tag("b001+z0100.pi") == "pi"


# ── sql_clause_for_tag (espelhamento com classifier) ─────────────


def _matches(tag: str, sku: str) -> bool:
    return classify_sku_tag(sku) == tag


@pytest.mark.parametrize("tag,sku,expected", [
    # Overrides — sa pega os 6 dg/zr
    ("sa", "dg001.us", True),
    ("sa", "dg011.us", True),
    ("sa", "dg017.us", True),
    ("sa", "dg023.us", True),
    ("sa", "dg061.us", True),
    ("sa", "zr010.us", True),
    ("sa", "u200.sa", True),    # natural .sa
    ("sa", "z0099", False),
    # Overrides — us NÃO pega os SKUs redirecionados
    ("us", "dg001.us", False),
    ("us", "zr010.us", False),
    ("us", "b000.us", False),
    ("us", "z0097.us", False),
    # us pega z* SEM sufixo e .us natural
    ("us", "z0099", True),
    ("us", "z0101", True),
    ("us", "a001.us", True),
    # Overrides — mala pega b000.us e z0097.us
    ("mala", "b000.us", True),
    ("mala", "z0097.us", True),
    # Overrides — acessórios de mala a*
    ("mala", "a006", True),
    ("mala", "a015", True),
    ("mala", "a073", True),
    ("mala", "a074", True),
    ("mala", "a075", True),
    ("mala", "a076", True),
    ("mala", "a001", False),
    ("eletro", "a015", False),
    ("us", "a015", False),
    ("mala", "b001", True),      # natural
    ("mala", "b1234", True),
    ("mala", "bp001", True),     # mochila via override
    ("mala", "bp002", True),
    ("mala", "bp003", True),
    ("mala", "bp999", False),
    ("mala", "z0099", False),
    # Carve-outs continuam
    ("mala", "z0094.mala", True),
    ("eletro", "z0010.eletro", True),
    # Revert: regional não-us volta a casar z*.{regional}
    ("pi", "za100.pi", True),    # revert
    ("pi", "zdg005.pi", True),
    ("pi", "a001.pi", True),
    ("ra", "z0100.ra", True),    # revert
    ("ra", "zx024.ra", True),
    ("ra", "b045.ra", True),
    # Pi/Ra rejeitam SKU override de sa
    ("pi", "dg001.us", False),
    # eletro natural + carve-out
    ("eletro", "u001", True),
    ("eletro", "u300", True),
    ("eletro", "a001.us", False),
    # fake sem mudança
    ("fake", "fake.x", True),
    ("fake", "a001", False),
    # insumos nunca casa
    ("insumos", "anything", False),
])
def test_sql_clause_for_tag_matches_classifier(tag, sku, expected):
    # Espelha o classifier — se um pareia o esperado, o outro também.
    assert _matches(tag, sku) is expected
    # Smoke: clause compila sem erro pra todas as tags.
    clause = sql_clause_for_tag(_col, tag)
    assert clause is not None
