"""Fonte única da classificação SKU → tag de operador.

Tanto o Controle de Estoque (routers/estoque.py, lado SQL) quanto as
Devoluções (routers/devolutions.py, na escrita) derivam a tag a partir do
SKU aqui — assim as duas telas nunca divergem.

Vocabulário (SEM ponto à frente): ci/pi/ra/sa/sp/us/cd, fake, mala, eletro,
insumos. `insumos` ainda não tem padrão de SKU → nunca casa.

Precedência (sufixo vence):
  1. sufixo regional/usado   `.ci/.pi/.ra/.sa/.sp/.us/.cd`
  2. fake    prefixo  `fake.`
  3. mala    prefixo  `b` + dígito  (`^b[0-9]`) — exclui `bp001` (mochila)
  4. eletro  prefixo  `u`           (`^u`)
  5. resto → None

Kits (`a+b`) são avaliados componente a componente; a primeira regra que
casar QUALQUER componente vence, seguindo a precedência acima. Os dados de
prod confirmam que componentes `b`/`u` sempre aparecem no início do kit e
que kits com sufixo regional terminam nesse sufixo — por isso o clause SQL
(`^b[0-9]` / `^u` / `%.{suf}`) produz exatamente o mesmo resultado.
"""

from __future__ import annotations

import re

# Sufixos regionais/usado (mapeados por `.{tag}` no fim do SKU).
SUFFIX_TAGS = ("ci", "pi", "ra", "sa", "sp", "us", "cd")
# Tags por prefixo com ponto (`fake.`).
PREFIX_TAGS = ("fake",)
# Conjunto completo de tags válidas (usado para validar input do admin).
VALID_TAGS = frozenset({*SUFFIX_TAGS, *PREFIX_TAGS, "mala", "eletro", "insumos"})

_MALA_RE = re.compile(r"^b[0-9]")
_ELETRO_RE = re.compile(r"^u")


def _suffix_of(part: str) -> str | None:
    if "." in part:
        tail = part.rsplit(".", 1)[1].lower()
        if tail in SUFFIX_TAGS:
            return tail
    return None


def classify_sku_tag(sku: str | None) -> str | None:
    """Retorna a tag de operador de um SKU (sem ponto à frente), ou None."""
    if not sku:
        return None
    parts = [p.strip().lower() for p in sku.split("+") if p.strip()]
    if not parts:
        return None
    # 1. sufixo regional/usado (sufixo vence)
    for p in parts:
        tail = _suffix_of(p)
        if tail:
            return tail
    # 2. fake
    for p in parts:
        if p.startswith("fake."):
            return "fake"
    # 3. mala (b + dígito)
    for p in parts:
        if _MALA_RE.match(p):
            return "mala"
    # 4. eletro (u…)
    for p in parts:
        if _ELETRO_RE.match(p):
            return "eletro"
    return None


def sql_clause_for_tag(column, tag: str):
    """Expressão booleana SQLAlchemy que casa SKUs cuja tag de operador é
    `tag`. Espelha `classify_sku_tag()`. Como sufixo vence sobre prefixo,
    fake/mala/eletro excluem qualquer SKU que carregue um sufixo regional.

    Sufixos casam case-sensitive via ILIKE; os prefixos mala/eletro usam o
    operador regex case-insensitive (`~*`) porque o classificador Python
    normaliza para minúsculas.

    Nuance só em KITS de sufixo misto (`x.ci+y.pi`): o clause usa "termina
    em `.{tag}`" (último componente) enquanto `classify_sku_tag` usa o
    primeiro componente com sufixo. É inofensivo: o único caller (Controle
    de Estoque) já exclui kits (`formato='S'` + `sku NOT LIKE '%+%'`), então
    só recebe SKUs simples, onde primeiro == último == único componente.
    Para SKUs simples os dois são idênticos (validado contra prod).
    """
    from sqlalchemy import and_, literal

    if tag in SUFFIX_TAGS:
        return column.ilike(f"%.{tag}")

    # prefixo (fake/mala/eletro): exclui SKUs com sufixo regional (sufixo vence)
    not_suffixed = [column.notilike(f"%.{s}") for s in SUFFIX_TAGS]
    if tag == "fake":
        return and_(column.ilike("fake.%"), *not_suffixed)
    if tag == "mala":
        return and_(column.op("~*")("^b[0-9]"), *not_suffixed)
    if tag == "eletro":
        return and_(column.op("~*")("^u"), *not_suffixed)
    # insumos — sem padrão de SKU ainda.
    return literal(False)
