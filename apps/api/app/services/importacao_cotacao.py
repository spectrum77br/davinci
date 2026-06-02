"""Importação — fórmula do previsto BRL da aba Cotação (etapa 3).

A mesma fórmula roda no frontend (computed reativo) — esta versão
Python existe pra testes e potencial uso em batch jobs futuros.

    previsto_brl = valor_usd * (1 + frete_pct) * taxa_cambio + adicional

`frete_pct` depende do tipo de frete selecionado por produto:
    regular / swap / acessorios
"""
from __future__ import annotations

from decimal import Decimal
from typing import Literal

FreteType = Literal["regular", "swap", "acessorios"]


def previsto_brl(
    *,
    valor_usd: Decimal | float | None,
    frete_type: FreteType,
    taxa_cambio: Decimal | float,
    frete_regular_pct: Decimal | float,
    frete_swap_pct: Decimal | float,
    frete_acessorios_pct: Decimal | float,
    adicional: Decimal | float,
) -> Decimal | None:
    """Retorna o custo previsto em BRL pra um produto, ou None quando
    `valor_usd` é nulo/inválido (não dá pra calcular sem ele)."""
    if valor_usd is None:
        return None
    try:
        usd = Decimal(str(valor_usd))
    except (ArithmeticError, ValueError):
        return None
    if usd <= 0:
        return None
    pct_map: dict[FreteType, Decimal] = {
        "regular": Decimal(str(frete_regular_pct)),
        "swap": Decimal(str(frete_swap_pct)),
        "acessorios": Decimal(str(frete_acessorios_pct)),
    }
    pct = pct_map.get(frete_type)
    if pct is None:
        return None
    cambio = Decimal(str(taxa_cambio))
    adic = Decimal(str(adicional))
    return usd * (Decimal("1") + pct) * cambio + adic
