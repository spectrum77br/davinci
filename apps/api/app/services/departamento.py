"""Departamento de produto e de anúncio (Organização do DaVinci, 13/11/2026, item 2).

Produtos (e os Anúncios, que viraram aba de Produtos) se dividem como a Tabela
de Preços: Celular · Mala · Eletro, mais o "Sem departamento" — a lista do que
falta classificar.

1. Manda o SEGMENTO. O departamento sai da RAIZ do segmento do produto, pelo
   slug (que não muda quando a raiz é renomeada na tela):

       Celular / …   → celular
       Catálogo / …  → celular   (decidido em 12/11: Catálogo entra em Celular)
       Mala / …      → mala
       Eletro / …    → eletro

   Segmento desativado continua no departamento da raiz (a Mala "Queima de
   estoque" desativada é Mala). O reserva abaixo nunca passa por cima de um
   segmento de raiz conhecida.

2. RESERVA (15/11/2026). Produto SEM segmento, ou com raiz fora dessas quatro,
   tem o departamento deduzido pela regra que o DaVinci já usa no Resumo da
   Garantia — sem ela, os ~1.500 celulares que nenhuma regra põe numa folha e
   todo produto novo do Bling (que nasce sem segmento) iam para "Sem
   departamento". Em ordem:

   a. a categoria do Bling: `products.categoria_nome`; se ela não decide, a do
      cadastro (`product_categories.name` pelo `products.category`). Começa com
      Celular ou Acessório → celular, Eletro → eletro, Mala → mala
      (garantia_resumo.departamento_da_categoria / PREFIXOS_DA_CATEGORIA);
   b. senão, a família do SKU:
      - o SKU exato que o sku_tags manda para a mala (a006, a015, a073–a080,
        bp001–bp003, b000.us…: `_SKU_TAG_OVERRIDES`) — o a0NN seria Celular;
      - o componente z… terminado em .mala / .eletro (regra 0 do sku_tags);
      - `dg` + dígito e `a0NN` → celular, `u` → eletro, `b`/`bp` + dígito →
        mala (garantia_resumo.FAMILIAS_DO_SKU);
      - `i` + dígito (iPhone, a família Apple do troca_sugestoes) → celular;
   c. nada disso → "sem".

A regra existe em Python (`departamento_do_produto`) e em SQL
(`expressao_departamento`, um CASE com `~`/`~*`), que é o que filtra a lista
de Produtos, conta os departamentos (/api/products/departamentos), filtra os
Anúncios pelo produto vinculado (`condicao_anuncio`) e o "Remover os N
mortos" (`condicao_vinculo`). tests/test_produtos_departamento.py confere as
duas, lado a lado, em SKUs e categorias reais. Única diferença conhecida: o
Python tira o acento da categoria por NFKD; o SQL, pela tabela de acentos do
português (a do garantia.sem_acento_sql, mais as maiúsculas).

O anúncio não tem segmento: vale o departamento do produto vinculado
(listings.product_id); anúncio sem produto vinculado vai para "sem".
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import Text, case, cast, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Listing, Product, ProductCategory, ProductLink
from app.services.garantia import _ACENTOS, _SEM_ACENTO
from app.services.garantia_resumo import (
    FAMILIAS_DO_SKU,
    PREFIXOS_DA_CATEGORIA,
    departamento_da_categoria,
    departamento_da_familia,
)
from app.services.pricing.audit import _load_segment_roots
from app.services.sku_tags import _SKU_TAG_OVERRIDES

DEPARTAMENTOS: tuple[str, ...] = ("celular", "mala", "eletro", "sem")
SEM = "sem"
RAIZ_PARA_DEPARTAMENTO: dict[str, str] = {
    "celular": "celular",
    "catalogo": "celular",
    "mala": "mala",
    "eletro": "eletro",
}

# Família do SKU, além da tabela do garantia_resumo (a ordem é a do reserva):
# o SKU exato do sku_tags que aponta para um departamento (hoje, só mala).
_SKU_EXATO: dict[str, str] = {
    sku: tag for sku, tag in _SKU_TAG_OVERRIDES.items() if tag in DEPARTAMENTOS
}
# A regra 0 do sku_tags (AVULSO SALVADO): componente z… com esse fim.
_Z_SUFIXOS: tuple[str, ...] = ("mala", "eletro")
# iPhone: `i` + dígito (troca_sugestoes._familia, a família Apple) é Celular.
_RX_APPLE = re.compile(r"^i\d", re.I)
# O que o str.strip() do Python tira das pontas do SKU, no btrim do SQL.
_ESPACOS = " \t\r\n"
# A tabela de acentos do garantia.sem_acento_sql mais as maiúsculas: o lower()
# do Postgres não baixa a maiúscula acentuada em todo locale.
_COM_ACENTO_SQL = _ACENTOS + _ACENTOS.upper()
_SEM_ACENTO_SQL = _SEM_ACENTO * 2


def validar(departamento: str | None) -> str | None:
    """None (sem filtro) ou um dos DEPARTAMENTOS; outro valor → 400."""
    if departamento is None or departamento == "":
        return None
    if departamento not in DEPARTAMENTOS:
        raise HTTPException(400, detail={"code": "invalid_departamento"})
    return departamento


async def indice_departamentos(session: AsyncSession) -> dict[UUID, str]:
    """{segment_id: departamento} dos segmentos com raiz conhecida (os outros
    ficam de fora e, por isso, caem no reserva)."""
    raizes = await _load_segment_roots(session)
    return {
        sid: RAIZ_PARA_DEPARTAMENTO[slug]
        for sid, slug in raizes.items()
        if slug in RAIZ_PARA_DEPARTAMENTO
    }


# ── Python (PURAS) ────────────────────────────────────────────────────────────


def departamento_do_sku(sku: str | None) -> str | None:
    """A família do SKU (item b do docstring do módulo); None quando nenhuma
    regra decide. PURA."""
    s = (sku or "").strip().lower()
    if not s:
        return None
    if (dep := _SKU_EXATO.get(s)) is not None:
        return dep
    partes = [p.strip() for p in s.split("+")]
    for sufixo in _Z_SUFIXOS:
        if any(p.startswith("z") and p.endswith(f".{sufixo}") for p in partes):
            return sufixo
    if (dep := departamento_da_familia(s)) is not None:
        return dep
    return "celular" if _RX_APPLE.match(s) else None


def departamento_reserva(
    sku: str | None,
    categoria_nome: str | None = None,
    categoria_cadastro: str | None = None,
) -> str:
    """O departamento do produto sem segmento de raiz conhecida: a categoria
    do Bling, a do cadastro, a família do SKU; nada disso → "sem". PURA."""
    return (
        departamento_da_categoria(categoria_nome)
        or departamento_da_categoria(categoria_cadastro)
        or departamento_do_sku(sku)
        or SEM
    )


def departamento_do_produto(
    segment_id: UUID | None,
    idx: dict[UUID, str],
    sku: str | None,
    categoria_nome: str | None = None,
    categoria_cadastro: str | None = None,
) -> str:
    """O segmento de raiz conhecida manda; sem ele, o reserva. A gêmea em SQL
    é `expressao_departamento`. PURA."""
    if segment_id is not None and (dep := idx.get(segment_id)) is not None:
        return dep
    return departamento_reserva(sku, categoria_nome, categoria_cadastro)


def somar(linhas: Iterable[tuple[str | None, int]]) -> dict[str, int]:
    """[(departamento, quantidade)] → {celular, mala, eletro, sem}."""
    out = dict.fromkeys(DEPARTAMENTOS, 0)
    for departamento, quantidade in linhas:
        out[departamento if departamento in out else SEM] += int(quantidade or 0)
    return out


# ── SQL ───────────────────────────────────────────────────────────────────────


def _sql_da_categoria(nome: Any):
    """departamento_da_categoria em SQL: NULL quando não decide."""
    plano = func.lower(func.translate(nome, _COM_ACENTO_SQL, _SEM_ACENTO_SQL))
    return case(
        *((plano.op("~")(rf"^\s*{prefixo}"), dep) for prefixo, dep in PREFIXOS_DA_CATEGORIA),
        else_=None,
    )


def _sql_do_sku(sku: Any):
    """departamento_do_sku em SQL: NULL quando não decide."""
    s = func.lower(func.btrim(sku, _ESPACOS))
    ramos: list[tuple[Any, str]] = []
    for dep in DEPARTAMENTOS:
        exatos = sorted(k for k, v in _SKU_EXATO.items() if v == dep)
        if exatos:
            ramos.append((s.in_(exatos), dep))
    ramos += [(s.op("~")(rf"(^|\+)\s*z[^+]*\.{suf}\s*(\+|$)"), suf) for suf in _Z_SUFIXOS]
    ramos += [(s.op("~*")(rx.pattern), dep) for rx, dep in FAMILIAS_DO_SKU]
    ramos.append((s.op("~*")(_RX_APPLE.pattern), "celular"))
    return case(*ramos, else_=None)


def _sql_da_categoria_do_cadastro():
    """O departamento da categoria do cadastro: product_categories.name pelo
    products.category (o id do Bling), junta igual à do garantia_resumo;
    correlacionada ao Product da consulta de fora."""
    return (
        select(_sql_da_categoria(ProductCategory.name))
        .where(cast(ProductCategory.bling_category_id, Text) == func.btrim(Product.category))
        .limit(1)
        .scalar_subquery()
    )


def expressao_reserva():
    """departamento_reserva em SQL (sempre um dos DEPARTAMENTOS)."""
    return func.coalesce(
        _sql_da_categoria(Product.categoria_nome),
        _sql_da_categoria_do_cadastro(),
        _sql_do_sku(Product.sku),
        literal(SEM),
    )


def expressao_departamento(idx: dict[UUID, str]):
    """departamento_do_produto em SQL, sobre o Product da consulta: o
    segmento de raiz conhecida, senão o reserva. Com o Product de um OUTER
    JOIN vazio (anúncio sem produto), dá "sem"."""
    por_dep: dict[str, list[UUID]] = {}
    for sid, dep in idx.items():
        por_dep.setdefault(dep, []).append(sid)
    reserva = expressao_reserva()
    if not por_dep:
        return reserva
    return case(
        *((Product.segment_id.in_(ids), dep) for dep, ids in por_dep.items()),
        else_=reserva,
    )


def condicao_produto(departamento: str, idx: dict[UUID, str]):
    """Predicado SQL para Product: o produto cai no departamento."""
    return expressao_departamento(idx) == departamento


def condicao_anuncio(departamento: str, idx: dict[UUID, str]):
    """Predicado SQL para Listing: pelo departamento do produto vinculado; o
    anúncio sem produto vinculado é "sem"."""
    do_produto = (
        select(Product.id)
        .where(Product.id == Listing.product_id, condicao_produto(departamento, idx))
        .exists()
    )
    if departamento == SEM:
        return or_(Listing.product_id.is_(None), do_produto)
    return do_produto


def condicao_vinculo(departamento: str, idx: dict[UUID, str]):
    """Predicado SQL para ProductLink: pelo departamento do produto do vínculo
    (o "Remover os N mortos" da tela de Produtos, por departamento)."""
    return ProductLink.product_id.in_(select(Product.id).where(condicao_produto(departamento, idx)))
