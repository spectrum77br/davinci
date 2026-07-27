"""Resolver do CATÁLOGO de mala (Fase 3a) — dá o valor CHEIO da NF de uma mala
por (modelo, tamanho), casando o SKU do pedido com o `nf_catalogo_mala`.

A NF cheia de mala NÃO usa o valor de venda: usa o valor fixo do catálogo. O
catálogo é chaveado por `sku_base` (código-base do SKU, ex. `b001`) + `tamanho`.
O SKU do pedido carrega base+tamanho (`b001.20`), então o resolver quebra o SKU,
casa a base e o tamanho (aceitando FAIXAS do catálogo, ex. `08.10` cobre 8 e 10)
e devolve o valor unitário. Sem vínculo/sem match → None (o motor cai no valor
de venda, comportamento seguro).

Só resolve SKU de mala de UM tamanho (`b001.20`). Kit (`b001.12.18`), o kit-6
cheio (`b001`) e avulsos (`b001.12us`) NÃO resolvem — cada componente do kit já
é explodido em linhas antes de chegar aqui, quando aplicável.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import NfCatalogoMala


def parse_sku_mala(sku: str | None) -> tuple[str, int] | None:
    """Quebra um SKU de mala em (base, tamanho). Só aceita UM tamanho numérico.

    `b001.20` → ("b001", 20). `b001.12.18` (kit) / `b001` (kit-6) /
    `b001.12us` (avulso) → None.
    """
    if not sku:
        return None
    partes = sku.strip().split(".")
    if len(partes) != 2:
        return None
    base, tam = partes[0].strip().lower(), partes[1].strip()
    if not base or not tam.isdigit():
        return None
    return base, int(tam)


def _tamanhos_da_faixa(tamanho: str | None) -> set[int]:
    """Converte o `tamanho` do catálogo num conjunto de números. `08.10` → {8,10},
    `20` → {20}, None/inválido → conjunto vazio."""
    if not tamanho:
        return set()
    nums: set[int] = set()
    for seg in tamanho.split("."):
        seg = seg.strip()
        if seg.isdigit():
            nums.add(int(seg))
    return nums


def valor_para(
    linhas: list[NfCatalogoMala], sku: str | None
) -> Decimal | None:
    """Casa o SKU nas `linhas` do catálogo (já carregadas) e devolve o valor
    unitário cheio, ou None. Prefere match de tamanho EXATO (uma polegada só)
    sobre FAIXA, pra o específico vencer o agrupado."""
    parsed = parse_sku_mala(sku)
    if parsed is None:
        return None
    base, tam = parsed
    exato: Decimal | None = None
    faixa: Decimal | None = None
    for lin in linhas:
        if (lin.sku_base or "").strip().lower() != base:
            continue
        nums = _tamanhos_da_faixa(lin.tamanho)
        if tam not in nums:
            continue
        if len(nums) == 1:
            exato = lin.valor
        elif faixa is None:
            faixa = lin.valor
    return exato if exato is not None else faixa


async def carregar_por_bases(
    session: AsyncSession, bases: set[str]
) -> list[NfCatalogoMala]:
    """Carrega as linhas do catálogo cujas `sku_base` estão em `bases` (lower)."""
    bases = {b.strip().lower() for b in bases if b and b.strip()}
    if not bases:
        return []
    rows = (
        await session.execute(
            select(NfCatalogoMala).where(
                func.lower(func.trim(NfCatalogoMala.sku_base)).in_(bases)
            )
        )
    ).scalars().all()
    return list(rows)
