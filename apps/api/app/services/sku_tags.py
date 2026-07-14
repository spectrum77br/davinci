"""Fonte única da classificação SKU → tag de operador.

Tanto o Controle de Estoque (routers/estoque.py, lado SQL) quanto as
Devoluções (routers/devolutions.py, na escrita) derivam a tag a partir do
SKU aqui — assim as duas telas nunca divergem.

Vocabulário (SEM ponto à frente): ci/pi/ra/sa/sp/us/cd, fake, mala, eletro,
insumos. `insumos` ainda não tem padrão de SKU → nunca casa.

Precedência:
  -1. `_SKU_TAG_OVERRIDES` (SKU exato vence tudo — manter SKU do Bling
      mas direcionar pra outra tag)
   0. z (prefixo): z*.mala→mala / z*.eletro→eletro / z* SEM sufixo → us
                   (z* COM sufixo regional cai na regra 1)
   1. sufixo regional   `.ci/.pi/.ra/.sa/.sp/.us/.cd`  (z*.pi cai aqui)
   2. fake    prefixo  `fake.`
   3. mala    prefixo  `b` + dígito  (`^b[0-9]`) — exclui `bp001` (mochila)
   4. eletro  prefixo  `u`           (`^u`)
   5. resto → None

Kits (`a+b`) são avaliados componente a componente; a primeira regra que
casar QUALQUER componente vence, seguindo a precedência acima — incluindo
a regra 0. Os dados de prod confirmam que componentes `b`/`u` sempre
aparecem no início do kit e que kits com sufixo regional terminam nesse
sufixo — por isso o clause SQL (`^b[0-9]` / `^u` / `%.{suf}`) produz
exatamente o mesmo resultado.
"""

from __future__ import annotations

import re

# Sufixos regionais/usado (mapeados por `.{tag}` no fim do SKU).
SUFFIX_TAGS = ("ci", "pi", "ra", "sa", "sp", "us", "cd")
# Tags por prefixo com ponto (`fake.`).
PREFIX_TAGS = ("fake",)
# Conjunto completo de tags válidas (usado para validar input do admin).
VALID_TAGS = frozenset({*SUFFIX_TAGS, *PREFIX_TAGS, "mala", "eletro", "insumos"})

# Overrides por SKU exato. Operador quer manter o nome do Bling (sem
# renomear) mas direcionar pra outra tag. Vence sufixo, prefixo, regra
# do z — vence tudo. Lowercase pra bater com a normalização do
# classifier (`p.strip().lower()`).
_SKU_TAG_OVERRIDES: dict[str, str] = {
    # 6 itens .us que pertencem a sa
    "dg001.us": "sa",
    "dg011.us": "sa",
    "dg017.us": "sa",
    "dg023.us": "sa",
    "dg061.us": "sa",
    "zr010.us": "sa",
    # 3 malas que ficaram com sufixo .us
    "b000.us": "mala",
    "z0097.us": "mala",
    # z0207.mala.us = "Mala Losangulo M4 ... Avariada AVULSO SALVADO": o
    # sufixo .us venceria o infixo .mala e jogava a mala pro estoque de
    # usados. Override manda pra tag mala.
    "z0207.mala.us": "mala",
}

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
    # Override por SKU exato — precedência máxima. Avaliado sobre o SKU
    # inteiro (não componente de kit) porque é uma lista hand-curada.
    sku_lower = sku.strip().lower()
    if sku_lower in _SKU_TAG_OVERRIDES:
        return _SKU_TAG_OVERRIDES[sku_lower]
    parts = [p.strip().lower() for p in sku.split("+") if p.strip()]
    if not parts:
        return None
    # 0. Prefixo z (AVULSO SALVADO). Só vale pra z*.mala (mala),
    #    z*.eletro (eletro), ou z* SEM sufixo (→ us). z* COM sufixo
    #    regional (z*.pi, z*.ra, z*.us, etc.) NÃO casa aqui — cai na
    #    regra 1 e o sufixo vence.
    for p in parts:
        if not p.startswith("z"):
            continue
        if p.endswith(".mala"):
            return "mala"
        if p.endswith(".eletro"):
            return "eletro"
        if "." not in p:
            return "us"
        # tem ponto = tem sufixo → não casa a regra 0, deixa a regra 1
        # (sufixo) cuidar. Continuamos o loop pra procurar outro
        # componente do kit que possa casar a regra 0.
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
    from sqlalchemy import and_, func, literal, or_

    def _apply_sku_overrides(base, column, tag):
        """Aplica os overrides do classifier no SQL:
          - SKUs cujo override aponta pra `tag` ENTRAM (OR);
          - SKUs cujo override aponta pra OUTRA tag SAEM (AND NOT IN).
        Mantém o classifier e o clause batendo 1:1 pros 8 SKUs da
        lista. lower() pro `func.lower(column).in_` casar a normalização
        do classifier."""
        to_this = sorted(s for s, t in _SKU_TAG_OVERRIDES.items() if t == tag)
        away = sorted(s for s, t in _SKU_TAG_OVERRIDES.items() if t != tag)
        clause = base
        if to_this:
            clause = or_(clause, func.lower(column).in_(to_this))
        if away:
            clause = and_(clause, func.lower(column).notin_(away))
        return clause

    # Regra 0 do classifier no SQL: z* SEM sufixo (sem ponto) vai pra
    # `us`; z*.mala / z*.eletro entram nas tags correspondentes via OR.
    # z* COM sufixo regional NÃO é tratado aqui — cai na regra 1 (clause
    # de sufixo) sem precisar excluir explicitamente.
    is_z = column.op("~*")("^z")
    z_no_suffix = and_(is_z, column.notlike("%.%"))
    z_mala = and_(is_z, column.ilike("%.mala"))
    z_eletro = and_(is_z, column.ilike("%.eletro"))

    if tag in SUFFIX_TAGS:
        suffix_match = column.ilike(f"%.{tag}")
        if tag == "us":
            # `.us` natural + z* SEM sufixo (z0099, z0101 etc.) caem aqui.
            base = or_(suffix_match, z_no_suffix)
        else:
            # Regional não-us: z*.{regional} agora casa naturalmente
            # via suffix_match (revert do exclude anterior).
            base = suffix_match
        return _apply_sku_overrides(base, column, tag)

    # prefixo (fake/mala/eletro): exclui SKUs com sufixo regional (sufixo vence)
    not_suffixed = [column.notilike(f"%.{s}") for s in SUFFIX_TAGS]
    if tag == "fake":
        base = and_(column.ilike("fake.%"), *not_suffixed)
        return _apply_sku_overrides(base, column, "fake")
    if tag == "mala":
        # Mala "natural" (^b[0-9] sem sufixo) ∪ z*.mala (malas usadas).
        base = or_(
            and_(column.op("~*")("^b[0-9]"), *not_suffixed),
            z_mala,
        )
        return _apply_sku_overrides(base, column, "mala")
    if tag == "eletro":
        base = or_(
            and_(column.op("~*")("^u"), *not_suffixed),
            z_eletro,
        )
        return _apply_sku_overrides(base, column, "eletro")
    # insumos — sem padrão de SKU ainda. Não tem override que aponte
    # pra cá, então não precisa de _apply_sku_overrides.
    return literal(False)
