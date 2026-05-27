"""Importação — deterministic name generation for malas.

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
