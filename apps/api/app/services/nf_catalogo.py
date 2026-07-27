"""Resolver do CATÁLOGO de mala (Fase 3a) — dá o valor CHEIO da NF de uma mala
por (modelo, tamanho), casando o SKU do pedido com o `nf_catalogo_mala`.

A NF cheia de mala NÃO usa o valor de venda: usa o valor fixo do catálogo. O
catálogo é chaveado por `modelo` (rótulo fiscal: abs / pp / me1 / me2) + `tamanho`.
A família da mala (M1..M6, P1..P6, ME1, ME2) vem do NOME do produto e mapeia pro
rótulo do catálogo (M1..M6 → abs, P1..P6 → pp, ME1 → me1, ME2 → me2), então o
casamento é AUTOMÁTICO pelo SKU, sem vínculo manual. O SKU do pedido carrega
base+tamanho (`b001.20`): o resolver quebra o tamanho, casa `modelo` + `tamanho`
(aceitando FAIXAS do catálogo, ex. `08.10` cobre 8 e 10) e devolve o valor
unitário. Sem modelo/sem match → None (o motor cai no valor de venda, seguro).

Só resolve SKU de mala de UM tamanho (`b001.20`). Kit (`b001.12.18`), o kit-6
cheio (`b001`) e avulsos (`b001.12us`) NÃO resolvem — cada componente do kit já
é explodido em linhas antes de chegar aqui, quando aplicável.
"""

from __future__ import annotations

import re
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import NfCatalogoMala

# Família da mala (do nome do produto) → rótulo `modelo` do catálogo.
_FAMILIA_MODELO: dict[str, str] = {
    "m1": "abs", "m2": "abs", "m3": "abs", "m4": "abs", "m5": "abs", "m6": "abs",
    "p1": "pp", "p2": "pp", "p3": "pp", "p4": "pp", "p5": "pp", "p6": "pp",
    "me1": "me1", "me2": "me2",
}
# ME[12] antes de M[1-6] (alternância) pra "ME2" não casar como "M".
_FAMILIA_RE = re.compile(r"\b(ME[12]|M[1-6]|P[1-6])\b", re.IGNORECASE)


def modelo_do_nome(nome: str | None) -> str | None:
    """Extrai a família do NOME do produto (ex. 'Mala Lisa M2 ...' → 'm2') e
    mapeia pro rótulo `modelo` do catálogo ('abs'). None se não achar família
    conhecida (P7/P8 e outros ficam de fora → motor cai no valor de venda)."""
    if not nome:
        return None
    m = _FAMILIA_RE.search(nome)
    if not m:
        return None
    return _FAMILIA_MODELO.get(m.group(1).lower())


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
    linhas: list[NfCatalogoMala], sku: str | None, modelo: str | None
) -> Decimal | None:
    """Casa o SKU (via `modelo`/família já resolvida) nas `linhas` do catálogo e
    devolve o valor unitário cheio, ou None. Prefere match de tamanho EXATO (uma
    polegada só) sobre FAIXA, pra o específico vencer o agrupado."""
    parsed = parse_sku_mala(sku)
    if parsed is None or not modelo:
        return None
    _base, tam = parsed
    modelo = modelo.strip().lower()
    exato: Decimal | None = None
    faixa: Decimal | None = None
    for lin in linhas:
        if (lin.modelo or "").strip().lower() != modelo:
            continue
        nums = _tamanhos_da_faixa(lin.tamanho)
        if tam not in nums:
            continue
        if len(nums) == 1:
            exato = lin.valor
        elif faixa is None:
            faixa = lin.valor
    return exato if exato is not None else faixa


async def carregar_todos(session: AsyncSession) -> list[NfCatalogoMala]:
    """Carrega todas as linhas do catálogo (tabela pequena, casada em memória)."""
    rows = (await session.execute(select(NfCatalogoMala))).scalars().all()
    return list(rows)
