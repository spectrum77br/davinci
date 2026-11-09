"""Pure stock-family identity used to interpret historical lot substitutions.

Business rule: services/estoque_familia.py. Every kit component participates in
the key. Distribution-center, used and mixed lots have no sale-family identity.
"""

SALE_LOTS = frozenset({"ci", "pi", "ra", "sa", "sp"})
KNOWN_LOTS = SALE_LOTS | {"cd", "us"}


def lot(part):
    suffix = part.rsplit(".", 1)[-1] if "." in part else ""
    return suffix if suffix in KNOWN_LOTS else None


def key(sku):
    raw = (sku or "").strip().lower()
    if not raw or raw.startswith("fake."):
        return None
    parts = [part.strip() for part in raw.split("+") if part.strip()]
    lots = {lot(part) for part in parts} - {None}
    if len(lots) != 1:
        return None
    chosen = next(iter(lots))
    if chosen not in SALE_LOTS:
        return None
    return "+".join(part[: -(len(chosen) + 1)] if lot(part) == chosen else part for part in parts)
