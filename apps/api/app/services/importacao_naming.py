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
