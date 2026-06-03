"""Fórmula de reposição em `_compute_product_fields`.

Garante o gate por estoque (consumo_diario vs maior_media_30d) e que a
multiplicação final usa `memoria` — não `consumo_diario` direto. O caso
crítico é o de ruptura: estoque=0 + consumo_diario=0, com demanda real
registrada em maior_media_30d. Antes do fix, reposição zerava nesse
cenário e o produto sumia da lista de compra.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace


def _cfg(tempo_reposicao: int = 150, tempo_estoque: int = 60):
    """ImportConfig stub — só os 2 campos lidos pela função."""
    return SimpleNamespace(tempo_reposicao=tempo_reposicao, tempo_estoque=tempo_estoque)


def _product(*, estoque, consumo, media):
    """ImportProduct stub — só os 3 campos lidos pela função."""
    return SimpleNamespace(
        estoque_bling=estoque,
        consumo_diario=Decimal(str(consumo)) if consumo is not None else None,
        maior_media_30d=Decimal(str(media)) if media is not None else None,
    )


def test_estoque_positivo_consumo_regular():
    """Estoque > 0 → memoria = consumo. Cenário regular sem ruptura.

    estoque=100, consumo=2/dia, media=2.5, necessario=30 dias
    duracao = 100/2 = 50 dias
    saldo_dias = 30 - 50 = -20 → reposicao = -20 * 2 = -40 (já tem sobra)
    """
    from app.routers.importacao import _compute_product_fields

    p = _product(estoque=100, consumo=2, media=Decimal("2.5"))
    cfg = _cfg(tempo_reposicao=20, tempo_estoque=10)
    memoria, reposicao, saldo = _compute_product_fields(p, cfg, pedidos_em_aberto=0)
    assert memoria == Decimal("2")  # estoque > 0 → consumo
    assert reposicao == -40
    assert saldo == -40


def test_estoque_zero_com_media_gera_reposicao_positiva():
    """Crítico: estoque=0 e consumo=0 (ruptura), mas media>0.

    Antes do fix: reposicao = saldo_dias * consumo = 30 * 0 = 0 (BUG)
    Agora: reposicao = saldo_dias * memoria = 30 * 5 = 150

    estoque=0, consumo=0, media=5, necessario=30
    memoria = media = 5 (gate por estoque ≤ 0)
    duracao = 0 / 5 = 0
    saldo_dias = 30 - 0 = 30
    reposicao = 30 * 5 = 150
    """
    from app.routers.importacao import _compute_product_fields

    p = _product(estoque=0, consumo=0, media=5)
    cfg = _cfg(tempo_reposicao=20, tempo_estoque=10)
    memoria, reposicao, saldo = _compute_product_fields(p, cfg, pedidos_em_aberto=0)
    assert memoria == Decimal("5")  # estoque ≤ 0 → media
    assert reposicao == 150
    assert saldo == 150


def test_estoque_positivo_media_diferente_usa_consumo():
    """Estoque > 0 → memoria = consumo (ignora media mesmo se diferente).

    Confirma que o gate funciona: quando o estoque está saudável,
    consumo_diario é a fonte de verdade — maior_media_30d só entra em
    ruptura. Também valida que reposição usa `memoria` (= consumo aqui).

    estoque=50, consumo=4, media=10 (irrelevante), necessario=30
    memoria = consumo = 4
    duracao = 50/4 = 12.5
    saldo_dias = 30 - 12.5 = 17.5
    reposicao = 17.5 * 4 = 70
    """
    from app.routers.importacao import _compute_product_fields

    p = _product(estoque=50, consumo=4, media=10)
    cfg = _cfg(tempo_reposicao=20, tempo_estoque=10)
    memoria, reposicao, saldo = _compute_product_fields(p, cfg, pedidos_em_aberto=5)
    assert memoria == Decimal("4")
    assert reposicao == 70
    assert saldo == 65  # 70 - 5 pedidos em aberto
