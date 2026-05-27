"""Importação — deterministic name generation + kit pricing helpers.

The operator's planilha-mãe (IMPORTAÇÃO.xlsx aba malas) prescribes a
single naming convention for newly-created mala products:

    Mala {modelo_bling} tamanho {N} - {cor}

where N is the number after the dot in the SKU (e.g. SKU b042.28 → 28).

Edge cases the operator can produce:
  * SKU has no dot  → skip the "tamanho N" portion (no fake size).
  * SKU dot suffix not a number → skip the "tamanho N" portion.
  * cor missing → skip the "- cor" suffix.
  * modelo_bling missing → still emit "Mala" prefix + whatever else is present
    (operator sees a half-finished name and fills the gap).

The function is pure + deterministic — used by the router on create and
by the frontend (mirrored logic in importacao.vue) for the live preview.
"""
from __future__ import annotations

import re


def _size_from_sku(sku: str | None) -> str | None:
    """Returns the digits after the first '.' in the SKU, or None when
    there's no dot or the suffix isn't fully numeric. Whitespace is
    stripped; case is preserved (irrelevant for digits)."""
    if not sku:
        return None
    s = sku.strip()
    if "." not in s:
        return None
    suffix = s.split(".", 1)[1].strip()
    if not suffix or not suffix.isdigit():
        return None
    return suffix


def generate_mala_name(
    modelo_bling: str | None,
    sku: str | None,
    cor: str | None,
) -> str:
    """Builds the canonical mala display name.

    Always starts with 'Mala'. The other segments are appended only when
    their inputs are present + valid, so partial inputs produce a
    sensible partial name (instead of "Mala  tamanho  - ").
    """
    parts: list[str] = ["Mala"]

    modelo = (modelo_bling or "").strip()
    if modelo:
        parts.append(modelo)

    size = _size_from_sku(sku)
    if size:
        parts.append(f"tamanho {size}")

    base = " ".join(parts)

    cor_clean = (cor or "").strip()
    if cor_clean:
        return f"{base} - {cor_clean}"
    return base


def parse_kit_variation(code: str) -> tuple[list[str], list[str]]:
    """Quebra o code de uma variation de kit em (tamanhos_numericos, acessorios).

    Exemplos:
      * "8"                                  → (["8"], [])
      * "12,14,16"                           → (["12", "14", "16"], [])
      * "8+18"                               → (["8", "18"], [])
      * "12+20+24+a075+bp003+a076"           → (["12","20","24"], ["a075","bp003","a076"])
      * "8+12+20+24+a075+bp003+a076"         → (["8","12","20","24"], ["a075","bp003","a076"])

    Regra:
      * Separadores: "+" e "," (ambos tratados igual).
      * Parte 100% dígitos → tamanho numérico (mala).
      * Outra qualquer → acessório (vai direto como SKU).

    Whitespace é stripped. Partes vazias são puladas. Caso "12,14,16":
    o operador anotou esse code como "tamanho duplicado" (mesma família
    de mochilas/acessórios); cabe ao caller decidir como mapear pra
    SKU de componente (por convenção usamos o primeiro número).
    """
    if not code:
        return ([], [])
    # Normaliza separadores
    normalized = code.replace(",", "+")
    sizes: list[str] = []
    accessories: list[str] = []
    for piece in normalized.split("+"):
        p = piece.strip()
        if not p:
            continue
        if p.isdigit():
            sizes.append(p)
        else:
            accessories.append(p.lower())
    return (sizes, accessories)


# ── Fase 3 helpers (pricing_product sync) ────────────────────────────


def extract_sizes(variation_code: str) -> list[int]:
    """Lista de tamanhos numéricos da variation (em ordem). Ignora
    acessórios (a075, bp003, etc).

    "8+18"                              → [8, 18]
    "12,14,16"                          → [12, 14, 16]
    "12+20+24+a075+bp002+a076"          → [12, 20, 24]
    "8+12+13+18+20+24"                  → [8, 12, 13, 18, 20, 24]
    """
    if not variation_code:
        return []
    out: list[int] = []
    for part in re.split(r"[+,]", variation_code):
        s = part.strip()
        if s.isdigit():
            out.append(int(s))
    return out


def has_accessories(variation_code: str) -> bool:
    """True se a variation contém algum componente que NÃO é número
    (acessórios começam com letra: a075, bp002, etc)."""
    if not variation_code:
        return False
    for part in re.split(r"[+,]", variation_code):
        s = part.strip()
        if not s:
            continue
        if not s.isdigit():
            # Qualquer parte não-numérica é tratada como acessório.
            return True
    return False


# Variation especial que representa "kit completo" (todos os tamanhos
# da família). Convenção em prod: SKU é o base puro, sem suffix.
# Verificado via `SELECT name, sku FROM pricing_products
# WHERE name LIKE '% 8+12+13+18+20+24'`. Apenas essa variation tem
# esse comportamento — todas as outras seguem o padrão base.size.size.
_COMPLETE_KIT_VARIATION = "8+12+13+18+20+24"


def build_kit_pricing_sku(sku_base: str, variation_code: str) -> str:
    """Constrói o SKU literal de um pricing_product de kit pra esta
    base + variation. Convenções herdadas de pricing_products em prod:

      base="b045", variation="8+18"
        → "b045.8.18"
      base="b045", variation="8+12+20+24"
        → "b045.8.12.20.24"
      base="b045", variation="12,14,16"
        → "b045.12.14.16"  (vírgulas viram pontos, todos são tamanhos)
      base="b045", variation="12+20+24+a075+bp002+a076"
        → "b045.12.20.24+a075+bp002+a076"  (acessórios com '+' literal)
      base="b045", variation="8+12+13+18+20+24" (kit completo)
        → "b045"  (caso especial — SKU é base puro)
    """
    base = sku_base.strip()
    code = (variation_code or "").strip()
    if not code:
        return base
    if code == _COMPLETE_KIT_VARIATION:
        return base

    sizes = extract_sizes(code)
    # Lista ordenada de acessórios na ordem em que aparecem no code.
    accessories = [
        p.strip() for p in re.split(r"[+,]", code)
        if p.strip() and not p.strip().isdigit()
    ]
    size_part = ".".join(str(s) for s in sizes)
    sku = f"{base}.{size_part}" if size_part else base
    if accessories:
        sku = f"{sku}+{'+'.join(accessories)}"
    return sku


def kit_pricing_segment_slug(variation_code: str) -> str:
    """Slug do segmento (filho de 'mala') pra esta variation.

    Regras derivadas dos pricing_products em prod:
      * Tem acessórios     → '18-20' (todos '+ Acessorios' caem aqui)
      * max(sizes) ≥ 24    → '24-acima'
      * max ∈ (18, 20)     → '18-20'
      * max == 12          → '12'
      * max ≤ 8 ou vazio   → 'acessorios'  (ex: "maleta 8" cai aqui)
    """
    if has_accessories(variation_code):
        return "18-20"
    sizes = extract_sizes(variation_code)
    if not sizes:
        return "acessorios"
    max_s = max(sizes)
    if max_s >= 24:
        return "24-acima"
    if max_s in (18, 20):
        return "18-20"
    if max_s == 12:
        return "12"
    # max <= 8 (ou outros tamanhos pequenos)
    return "acessorios"


def kit_pricing_name(modelo_bling: str | None, variation_code: str) -> str:
    """Nome canônico do pricing_product pra esta combinação de
    modelo × variation. Espelha convenção em prod:

      * Variation com acessórios → "{FAMILY_PREFIX} + Acessorios"
        (FAMILY_PREFIX = primeiro token de modelo_bling: "M5", "P6",
        "ME1"). Ex: "M5 mista" → "M5 + Acessorios".
      * max(sizes) ≥ 18 → "{modelo} mala {variation}". Ex:
        "M5 mista mala 8+18".
      * max(sizes) ≤ 12 → "{modelo} maleta {variation}". Ex:
        "M5 mista maleta 12", "M5 mista maleta 8+12".

    Se modelo_bling for None/vazio, gera só o prefixo (degraded — o
    operador vai consertar manualmente vendo a row sem prefix).
    """
    modelo = (modelo_bling or "").strip()
    if has_accessories(variation_code):
        family = modelo.split()[0] if modelo else "?"
        return f"{family} + Acessorios"

    sizes = extract_sizes(variation_code)
    if sizes and max(sizes) >= 18:
        tipo = "mala"
    else:
        tipo = "maleta"

    parts = []
    if modelo:
        parts.append(modelo)
    parts.append(tipo)
    parts.append(variation_code)
    return " ".join(parts)


def generate_kit_name(
    modelo_bling: str | None,
    sku_base: str,
    variation_code: str,
    cor: str | None,
) -> str:
    """Builds the canonical kit (composto) product name.

    Padrão:
      "Kit Mala {modelo} tamanhos {tam1+tam2+...} - {Cor}"

    Quando o kit inclui acessórios (a075, bp003, etc), eles aparecem
    depois dos tamanhos: "tamanhos 12+20+24 + a075 + bp003 - Branca".

    Edge cases:
      * Sem tamanhos numéricos (só acessórios): "Kit {modelo} {acc} - {Cor}"
      * Acessório standalone (sku_base começa com a/bp): "Kit {modelo} - {Cor}"
        (variation_code raramente bate, mas é defensivo).
      * Sem modelo: "Kit tamanhos ..." (sem prefixo mala).
      * Sem cor: omite o sufixo " - {Cor}".
    """
    sizes, accessories = parse_kit_variation(variation_code)
    modelo = (modelo_bling or "").strip()
    cor_clean = (cor or "").strip()

    is_accessory_base = sku_base.lower().startswith(("a", "bp"))
    base_word = "Mala" if not is_accessory_base else ""

    parts: list[str] = ["Kit"]
    if base_word:
        parts.append(base_word)
    if modelo:
        parts.append(modelo)

    if sizes:
        parts.append(f"tamanhos {'+'.join(sizes)}")
    if accessories:
        parts.append("+ " + " + ".join(accessories))

    name = " ".join(parts).strip()
    if cor_clean:
        name = f"{name} - {cor_clean}"
    return name
