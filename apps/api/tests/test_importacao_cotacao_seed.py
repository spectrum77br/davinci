"""Migration 0124 popula 112 modelos celular com valor_brl_realizado /
valor_usd / frete_type a partir do Excel operacional.

Smoke check do conteúdo do seed (sem aplicar a migration — testa só
a tupla `_VALUES` direto do arquivo). Garante que os valores chave
estão lá e que o frete_type respeita o CHECK constraint do DB.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_migration():
    """Carrega a migration como módulo via importlib.util porque o
    nome começa com dígito (não é importável via `import_module`)."""
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic" / "versions" / "0124_seed_celular_cotacao_values.py"
    )
    spec = importlib.util.spec_from_file_location("_mig_0124", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_seed_tem_apple_ipad_amarelo():
    """Apple iPad Amarelo: valor_usd=340, valor_brl_realizado=2000,
    frete_type='regular'."""
    mod = _load_migration()
    rows = {r[0]: r[1:] for r in mod._VALUES}
    assert "Apple Ipad 11 128 GB - Amarelo" in rows
    brl, usd, frete = rows["Apple Ipad 11 128 GB - Amarelo"]
    assert brl == 2000.0
    assert usd == 340.0
    assert frete == "regular"


def test_seed_tem_iphone17_pro_azul_swap():
    """iPhone 17 Pro 256 GB - Azul: USD=1265, frete='swap'."""
    mod = _load_migration()
    rows = {r[0]: r[1:] for r in mod._VALUES}
    assert "Apple iPhone 17 Pro 256 GB - Azul" in rows
    brl, usd, frete = rows["Apple iPhone 17 Pro 256 GB - Azul"]
    assert usd == 1265.0
    assert frete == "swap"


def test_seed_so_usa_frete_types_validos():
    """Todos os frete_type ∈ {regular, swap, acessorios} (CHECK do DB)."""
    mod = _load_migration()
    allowed = {"regular", "swap", "acessorios"}
    for modelo, _brl, _usd, frete in mod._VALUES:
        assert frete in allowed, f"{modelo}: frete_type={frete!r} inválido"
