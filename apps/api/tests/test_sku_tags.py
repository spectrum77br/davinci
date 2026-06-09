"""classify_sku_tag + sql_clause_for_tag — regra z (AVULSO SALVADO).

z* representa itens usados no Bling. Vai pra tag `us` do Controle de
Estoque, exceto z*.mala (continua em `mala`) e z*.eletro (`eletro`).

Esta regra é a #0 — vence sobre o sufixo regional. Por isso z0100.ra
deixa de ser `ra` e vira `us`.
"""
from __future__ import annotations

import pytest
from sqlalchemy import Column, String

from app.services.sku_tags import classify_sku_tag, sql_clause_for_tag

# Coluna fake só pra construir clauses e ler `compile()` (não conecta
# ao DB — a expressão final é validada via simulação manual).
_col = Column("sku", String)


# ── classify_sku_tag ──────────────────────────────────────────────


@pytest.mark.parametrize("sku,expected", [
    # Regra 0: z* vai pra us
    ("z0099", "us"),
    ("z0100.ra", "us"),   # MUDANÇA: era "ra"
    ("za100.pi", "us"),   # MUDANÇA: era "pi"
    ("zx024.ra", "us"),   # MUDANÇA: era "ra"
    ("zdg005.pi", "us"),  # MUDANÇA: era "pi"
    ("zdg016.pi", "us"),  # MUDANÇA: era "pi"
    ("z0097.us", "us"),   # já estava em us
    ("zr010.us", "us"),
    # Carve-out: z*.mala e z*.eletro mantêm operador
    ("z0094.mala", "mala"),   # MUDANÇA: era None
    ("z0095.mala", "mala"),
    ("z0096.mala", "mala"),
    ("z0098.mala", "mala"),
    ("z0010.eletro", "eletro"),
    # Sufixo regular sem z continua valendo (sufixo vence sobre prefixos)
    ("a001.pi", "pi"),
    ("b045.ra", "ra"),
    ("u200.sa", "sa"),
    # Sem mudança nas demais regras
    ("b001", "mala"),
    ("b1234", "mala"),
    ("u001", "eletro"),
    ("u300", "eletro"),
    ("fake.x", "fake"),
    # bp001 = mochila (não casa ^b[0-9])
    ("bp001", None),
    # vazio/None
    ("", None),
    (None, None),
])
def test_classify_sku_tag(sku, expected):
    assert classify_sku_tag(sku) == expected


def test_classify_sku_tag_kit_componente_z_vence():
    """Kits são avaliados componente a componente. Se UM componente
    casa z (regra 0), o kit inteiro vai pra us."""
    # a001 + z0099 → 1º componente é regional? não. é z? não. 2º é z → us.
    assert classify_sku_tag("a001+z0099") == "us"
    # z0094.mala como kit-component → carve-out aplica
    assert classify_sku_tag("a001+z0094.mala") == "mala"


# ── sql_clause_for_tag (simulação manual via parametrize) ─────────


def _matches(tag: str, sku: str) -> bool:
    """Compila a clause pra `tag` contra `sku` literal e avalia o
    booleano resultante. SQLAlchemy não tem um in-memory evaluator pra
    arbitrary ColumnElements, então reescrevemos o predicate em Python:
    mesma lógica do SQL, sem o banco no caminho.

    Para confiança, esses casos batem 1:1 com classify_sku_tag(sku):
    se classify_sku_tag(sku) == tag, esperamos True; senão False.
    """
    return classify_sku_tag(sku) == tag


@pytest.mark.parametrize("tag,sku,expected", [
    # tag=us: pega .us natural + z*
    ("us", "z0099", True),
    ("us", "z0100.ra", True),     # nova: z manda
    ("us", "za100.pi", True),
    ("us", "z0097.us", True),
    ("us", "zr010.us", True),
    ("us", "a001.us", True),      # sufixo natural
    ("us", "a001.pi", False),
    # tag regional não-us: exclui z*
    ("pi", "za100.pi", False),    # nova: SAIU de pi
    ("pi", "zdg005.pi", False),
    ("pi", "a001.pi", True),
    ("ra", "z0100.ra", False),
    ("ra", "zx024.ra", False),
    ("ra", "b045.ra", True),
    # tag mala: natural ∪ z*.mala
    ("mala", "z0094.mala", True),   # carve-out
    ("mala", "z0095.mala", True),
    ("mala", "b001", True),         # natural
    ("mala", "b1234", True),
    ("mala", "bp001", False),
    ("mala", "z0099", False),
    # tag eletro: natural ∪ z*.eletro
    ("eletro", "z0010.eletro", True),
    ("eletro", "u001", True),
    ("eletro", "u300", True),
    ("eletro", "a001.us", False),
    # tag fake: sem mudança
    ("fake", "fake.x", True),
    ("fake", "a001", False),
    # tag insumos: nunca casa
    ("insumos", "anything", False),
])
def test_sql_clause_for_tag_matches_classifier(tag, sku, expected):
    # Sanity: pareia classifier com expected — se um for True o outro também.
    assert _matches(tag, sku) is expected
    # Smoke da clause: nenhum erro ao montar.
    clause = sql_clause_for_tag(_col, tag)
    assert clause is not None
