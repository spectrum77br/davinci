"""Departamento de produto e de anúncio (Organização do DaVinci, 13/11/2026, item 2).

Produtos (e os Anúncios, que viraram aba de Produtos) se dividem como a Tabela
de Preços: Celular · Mala · Eletro, mais o "Sem departamento" — a lista do que
falta classificar. O departamento sai da RAIZ do segmento do produto, pelo slug
(que não muda quando a raiz é renomeada na tela):

    Celular / …   → celular
    Catálogo / …  → celular   (decidido em 12/11: Catálogo entra em Celular)
    Mala / …      → mala
    Eletro / …    → eletro
    sem segmento  → sem

Raiz fora dessas quatro também cai em "sem", para nenhum produto sumir de todos
os botões. O anúncio não tem segmento: vale o do produto vinculado
(listings.product_id); anúncio sem produto vinculado vai para "sem".
Segmento desativado continua no departamento da raiz (a Mala "Queima de
estoque" desativada é Mala).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import false, not_, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Listing, Product
from app.services.pricing.audit import _load_segment_roots

DEPARTAMENTOS: tuple[str, ...] = ("celular", "mala", "eletro", "sem")
SEM = "sem"
RAIZ_PARA_DEPARTAMENTO: dict[str, str] = {
    "celular": "celular",
    "catalogo": "celular",
    "mala": "mala",
    "eletro": "eletro",
}


def validar(departamento: str | None) -> str | None:
    """None (sem filtro) ou um dos DEPARTAMENTOS; outro valor → 400."""
    if departamento is None or departamento == "":
        return None
    if departamento not in DEPARTAMENTOS:
        raise HTTPException(400, detail={"code": "invalid_departamento"})
    return departamento


async def indice_departamentos(session: AsyncSession) -> dict[UUID, str]:
    """{segment_id: departamento} dos segmentos com raiz conhecida (as outras
    ficam de fora e, por isso, contam como "sem")."""
    raizes = await _load_segment_roots(session)
    return {
        sid: RAIZ_PARA_DEPARTAMENTO[slug]
        for sid, slug in raizes.items()
        if slug in RAIZ_PARA_DEPARTAMENTO
    }


def departamento_do_segmento(segment_id: UUID | None, idx: dict[UUID, str]) -> str:
    if segment_id is None:
        return SEM
    return idx.get(segment_id, SEM)


def condicao_segmento(coluna: Any, departamento: str, idx: dict[UUID, str]):
    """Predicado SQL: a coluna `segment_id` (de Product) cai no departamento."""
    if departamento == SEM:
        mapeados = list(idx)
        if not mapeados:
            return true()
        return or_(coluna.is_(None), not_(coluna.in_(mapeados)))
    ids = [sid for sid, dep in idx.items() if dep == departamento]
    if not ids:
        return false()
    return coluna.in_(ids)


def condicao_anuncio(departamento: str, idx: dict[UUID, str]):
    """Predicado SQL para Listing: pelo segmento do produto vinculado; o
    anúncio sem produto vinculado é "sem"."""
    do_produto = (
        select(Product.id)
        .where(
            Product.id == Listing.product_id,
            condicao_segmento(Product.segment_id, departamento, idx),
        )
        .exists()
    )
    if departamento == SEM:
        return or_(Listing.product_id.is_(None), do_produto)
    return do_produto


def somar_por_departamento(
    linhas: list[tuple[UUID | None, int]], idx: dict[UUID, str]
) -> dict[str, int]:
    """[(segment_id, quantidade)] → {celular, mala, eletro, sem}."""
    out = dict.fromkeys(DEPARTAMENTOS, 0)
    for segment_id, quantidade in linhas:
        out[departamento_do_segmento(segment_id, idx)] += int(quantidade or 0)
    return out
