"""SKU do kit Celular propaga a tag do sku_base pros acessórios.

Tag = sufixo após o último '.' no sku_base. Representa loja/variante;
os acessórios existem no Bling como `a001.sa`, `a001.pi`, `a001.ra`,
etc. — o SKU do kit precisa apontar pra variante da MESMA tag.
"""
from __future__ import annotations

from app.services.bling_kit_create import _resolve_component_skus
from app.services.importacao_naming import (
    build_celular_kit_sku,
    celular_kit_components,
)


def test_build_celular_kit_sku_simple():
    assert build_celular_kit_sku("i205.sa", "a001") == "i205.sa+a001.sa"


def test_build_celular_kit_sku_combination():
    assert build_celular_kit_sku("i205.sa", "a003+a004") == "i205.sa+a003.sa+a004.sa"


def test_build_celular_kit_sku_different_tags():
    assert build_celular_kit_sku("dg052.ci", "a001") == "dg052.ci+a001.ci"
    assert build_celular_kit_sku("dg024.ra", "a003+a004") == "dg024.ra+a003.ra+a004.ra"


def test_build_celular_kit_sku_base_sem_tag_fallback():
    """sku_base sem '.' — sem tag pra propagar. Mantém comportamento
    antigo (sem sufixo) — guarda de borda; em prod todos celulares
    têm tag (.sa/.sp/.ra/.ci/.pi/.cd/.pp)."""
    assert build_celular_kit_sku("xyz", "a001") == "xyz+a001"


def test_resolve_components_celular_propaga_tag():
    assert _resolve_component_skus("i205.sa", "a001", categoria="celular") == [
        "i205.sa", "a001.sa",
    ]
    assert _resolve_component_skus(
        "dg052.ci", "a003+a004", categoria="celular",
    ) == ["dg052.ci", "a003.ci", "a004.ci"]


def test_celular_kit_components_direto():
    """celular_kit_components é o helper que o _resolve_component_skus
    delega — mesmo retorno."""
    assert celular_kit_components("i205.sa", "a001") == ["i205.sa", "a001.sa"]
    assert celular_kit_components("dg024.ra", "a003+a004") == [
        "dg024.ra", "a003.ra", "a004.ra",
    ]


def test_resolve_components_mala_inalterado():
    """Branch mala não muda — tamanhos numéricos viram sku_base.N."""
    assert _resolve_component_skus("b057", "8+18", categoria="mala") == [
        "b057.8", "b057.18",
    ]
    assert _resolve_component_skus("b057", "12+a075", categoria="mala") == [
        "b057.12", "a075",
    ]
