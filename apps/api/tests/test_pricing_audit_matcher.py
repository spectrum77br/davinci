"""Unit tests for `match_pricing_to_product_keys` in
`app.services.pricing.audit`. Pure function — no DB, no async — so a
SimpleNamespace mock of PricingProduct is enough.

Covers the mala bidirectional kit↔piece matching added on top of the
af9465c kit fallback. The fixtures mirror the real-prod pattern:
  * `b045.18`, `b046.18`, ... = single-size pieces (one of the "M5
    mista mala 18" SKUs)
  * `b045.12.18`, `b046.18.24`, ... = kits that contain those sizes
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from app.services.pricing.audit import (
    build_match_indexes,
    match_one_sku_to_keys,
    match_pricing_to_product_keys,
)


def _seg_roots(seg_id, slug):
    """Helper: build a segment_roots dict for the given dept slug."""
    return {seg_id: slug}


def _pp(sku, segment_id):
    """Build a minimal PricingProduct stand-in."""
    return SimpleNamespace(id=uuid4(), sku=sku, segment_id=segment_id)


# ── Mala — peça única ────────────────────────────────────────────────


def test_mala_single_piece_matches_kits_containing_that_size():
    """`b045.18` deve somar todos os kits b045.X.18 / b045.18.Y / etc."""
    seg = uuid4()
    roots = _seg_roots(seg, "mala")
    pp = _pp("b045.18", seg)
    keys = {
        "b045.12",        # mesma base, size diferente → não soma
        "b045.12.18",     # contém 18 → soma
        "b045.18.24",     # contém 18 → soma
        "b045.12.18.20",  # contém 18 → soma
        "b046.18",        # base diferente → não soma
    }
    res = match_pricing_to_product_keys([pp], keys, roots)
    assert res[pp.id] == {"b045.12.18", "b045.18.24", "b045.12.18.20"}


def test_mala_single_piece_with_exact_keeps_exact_plus_kits():
    """Quando o exact existe, soma tanto ele quanto os kits supersets."""
    seg = uuid4()
    roots = _seg_roots(seg, "mala")
    pp = _pp("b045.18", seg)
    keys = {"b045.18", "b045.12.18", "b045.18.24"}
    res = match_pricing_to_product_keys([pp], keys, roots)
    assert res[pp.id] == {"b045.18", "b045.12.18", "b045.18.24"}


def test_mala_single_piece_no_kits_no_exact_returns_empty():
    """Sem exact e sem kit superset, não há match → pp ausente do out."""
    seg = uuid4()
    roots = _seg_roots(seg, "mala")
    pp = _pp("b047.18", seg)
    keys = {"b045.12.18", "b046.18.24"}  # bases diferentes
    res = match_pricing_to_product_keys([pp], keys, roots)
    assert pp.id not in res


# ── Mala — kit ──────────────────────────────────────────────────────


def test_mala_kit_matches_superset_kits_and_keeps_exact():
    """`b045.18.20` matches o próprio + kits que contenham {18,20}."""
    seg = uuid4()
    roots = _seg_roots(seg, "mala")
    pp = _pp("b045.18.20", seg)
    keys = {
        "b045.18.20",        # exact
        "b045.18.20.24",     # ⊇ {18,20} → soma
        "b045.12.18.20",     # ⊇ {18,20} → soma
        "b045.18.24",        # {18,24} ⊉ {18,20} → não soma
        "b045.18",           # subset, não superset → não soma
    }
    res = match_pricing_to_product_keys([pp], keys, roots)
    assert res[pp.id] == {"b045.18.20", "b045.18.20.24", "b045.12.18.20"}


def test_mala_kit_without_exact_falls_back_to_components():
    """Preserva o fallback do af9465c: kit ≥3 parts sem exact → soma
    componentes individuais."""
    seg = uuid4()
    roots = _seg_roots(seg, "mala")
    pp = _pp("b045.18.20", seg)
    keys = {"b045.18", "b045.20", "b045.24"}  # só componentes
    res = match_pricing_to_product_keys([pp], keys, roots)
    # Não tem superset porque nenhuma key tem {18,20} ⊆ sizes.
    # Tem componente fallback (≥3 parts e exact vazio).
    assert res[pp.id] == {"b045.18", "b045.20"}


def test_mala_kit_with_exact_does_not_trigger_component_fallback():
    """Quando exact tem match, o components fallback NÃO dispara
    (comportamento idêntico ao af9465c)."""
    seg = uuid4()
    roots = _seg_roots(seg, "mala")
    pp = _pp("b045.18.20", seg)
    keys = {"b045.18.20", "b045.18", "b045.20"}
    res = match_pricing_to_product_keys([pp], keys, roots)
    # Exact bate; superset (nenhum); fallback NÃO roda (exact não vazio).
    assert res[pp.id] == {"b045.18.20"}


# ── Mala — SKU composto com '+' ─────────────────────────────────────


def test_mala_plus_piece_is_skipped():
    """Pieces com '+' ficam zerados (decisão operacional)."""
    seg = uuid4()
    roots = _seg_roots(seg, "mala")
    pp = _pp("b045.18+a075", seg)
    keys = {"b045.18", "b045.12.18"}
    res = match_pricing_to_product_keys([pp], keys, roots)
    assert pp.id not in res


def test_mala_comma_separated_pieces_aggregate():
    """SKU com várias pieces: cada uma processa sua bidirecional."""
    seg = uuid4()
    roots = _seg_roots(seg, "mala")
    pp = _pp("b045.18,b046.18", seg)
    keys = {"b045.12.18", "b046.18.24", "b047.18"}
    res = match_pricing_to_product_keys([pp], keys, roots)
    assert res[pp.id] == {"b045.12.18", "b046.18.24"}


# ── Outros departamentos (não devem usar a lógica nova) ─────────────


def test_catalogo_only_exact_no_kit_expansion():
    """Catalogo: só exact, sem bidirecional."""
    seg = uuid4()
    roots = _seg_roots(seg, "catalogo")
    pp = _pp("b045.18", seg)
    keys = {"b045.18", "b045.12.18"}
    res = match_pricing_to_product_keys([pp], keys, roots)
    # Só exact. Nada do superset porque não é mala.
    assert res[pp.id] == {"b045.18"}


def test_catalogo_plus_piece_skipped():
    """Catalogo: '+' continua sendo pulado."""
    seg = uuid4()
    roots = _seg_roots(seg, "catalogo")
    pp = _pp("b045.18+a075", seg)
    keys = {"b045.18+a075", "b045.18"}
    res = match_pricing_to_product_keys([pp], keys, roots)
    assert pp.id not in res


def test_celular_uses_base_match():
    """Celular: usa base-SKU (tudo antes do primeiro ponto)."""
    seg = uuid4()
    roots = _seg_roots(seg, "celular")
    pp = _pp("i200", seg)
    keys = {"i200.sa", "i200.pi", "i300.sa"}
    res = match_pricing_to_product_keys([pp], keys, roots)
    # Base "i200" puxa todos os i200.*
    assert res[pp.id] == {"i200.sa", "i200.pi"}


def test_default_dept_only_exact():
    """Dept sem regra especial: só exact match."""
    seg = uuid4()
    roots = _seg_roots(seg, "eletro")
    pp = _pp("e001.18", seg)
    keys = {"e001.18", "e001.12.18"}
    res = match_pricing_to_product_keys([pp], keys, roots)
    # Só exact, mesmo padrão de número.
    assert res[pp.id] == {"e001.18"}


# ── Casos extremos ──────────────────────────────────────────────────


def test_empty_pricing_rows_returns_empty():
    res = match_pricing_to_product_keys([], {"b045.18"}, {})
    assert res == {}


def test_empty_keys_returns_empty():
    seg = uuid4()
    roots = _seg_roots(seg, "mala")
    pp = _pp("b045.18", seg)
    res = match_pricing_to_product_keys([pp], set(), roots)
    assert res == {}


def test_mala_piece_without_numeric_size_is_ignored_in_bidirectional():
    """SKU `b045.kit2` (size não-numérica) não dispara o superset.
    Mas o exact ainda funciona."""
    seg = uuid4()
    roots = _seg_roots(seg, "mala")
    pp = _pp("b045.kit2", seg)
    keys = {"b045.kit2", "b045.12.18"}
    res = match_pricing_to_product_keys([pp], keys, roots)
    # Só exact — piece_sizes vazio aborta o superset.
    assert res[pp.id] == {"b045.kit2"}


# ── Helpers standalone: match_one_sku_to_keys + build_match_indexes ─


def _indexes(keys):
    return build_match_indexes(keys)


def test_helper_mala_single_piece_finds_kits():
    bx, bc, bs = _indexes(["b045.12", "b045.12.18", "b045.18.24", "b046.18"])
    assert match_one_sku_to_keys("b045.18", "mala", bx, bc, bs) == {
        "b045.12.18", "b045.18.24",
    }


def test_helper_mala_single_piece_with_exact_keeps_exact():
    bx, bc, bs = _indexes(["b045.18", "b045.12.18", "b045.18.24"])
    assert match_one_sku_to_keys("b045.18", "mala", bx, bc, bs) == {
        "b045.18", "b045.12.18", "b045.18.24",
    }


def test_helper_mala_kit_only_superset_with_all_sizes():
    bx, bc, bs = _indexes(["b045.18.20", "b045.18.20.24", "b045.12.18.20", "b045.18.24"])
    # `b045.18.24` tem sizes {18,24}, não cobre {18,20} — não conta.
    assert match_one_sku_to_keys("b045.18.20", "mala", bx, bc, bs) == {
        "b045.18.20", "b045.18.20.24", "b045.12.18.20",
    }


def test_helper_mala_no_dot_sku_only_exact():
    """`a006` (sem ponto) só ganha exact — sem expansão."""
    bx, bc, bs = _indexes(["a006", "a006.12", "a006.18"])
    # Sem sizes, não vira superset. Só exact.
    assert match_one_sku_to_keys("a006", "mala", bx, bc, bs) == {"a006"}


def test_helper_mala_plus_returns_empty():
    bx, bc, bs = _indexes(["b045.18+a075", "b045.18", "b045.12.18"])
    assert match_one_sku_to_keys("b045.18+a075", "mala", bx, bc, bs) == set()


def test_helper_celular_uses_base_prefix():
    bx, bc, bs = _indexes(["dg078.sa", "dg078.pi", "dg079.sa"])
    assert match_one_sku_to_keys("dg078", "celular", bx, bc, bs) == {
        "dg078.sa", "dg078.pi",
    }


def test_helper_eletro_exact_only():
    """Dept não-mala não-celular: só exact, mesmo com padrão de número."""
    bx, bc, bs = _indexes(["xy.123", "xy.12.123"])
    assert match_one_sku_to_keys("xy.123", "eletro", bx, bc, bs) == {"xy.123"}


def test_helper_catalogo_plus_skipped():
    bx, bc, bs = _indexes(["a.b+c", "a.b"])
    assert match_one_sku_to_keys("a.b+c", "catalogo", bx, bc, bs) == set()


def test_build_indexes_skips_plus_in_size_index():
    """Keys com '+' entram em by_exact mas NÃO em by_base_sizes."""
    bx, _, bs = build_match_indexes(["b045.18+a075", "b045.18"])
    assert "b045.18+a075" in bx
    # by_base_sizes só inclui b045.18 (sem '+')
    assert bs["b045"] == [("b045.18", frozenset({"18"}))]


def test_build_indexes_skips_non_numeric_size():
    """Sizes não-numéricos (kit2, ra, ...) não entram em by_base_sizes."""
    _, _, bs = build_match_indexes(["b045.kit2", "b045.18"])
    # Só b045.18 (sizes numéricos) entra; b045.kit2 fica fora.
    assert bs.get("b045") == [("b045.18", frozenset({"18"}))]
