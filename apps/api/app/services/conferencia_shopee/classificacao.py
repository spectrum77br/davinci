"""O que é Eletro na Conferência Shopee — item a item (item_id da Shopee).

Uma conta de celular vende também eletro (air fryer, cafeteira, smart cooking,
cookware, slushie, sorveteira): o relatório tira a parte eletro da conta e põe
na seção Eletro (Celular = total − eletro). A decisão, nesta ordem:

  1. Vínculo do DaVinci — o anúncio (`product_links`, Shopee, vivo) aponta
     para produto(s) do DaVinci. É eletro se QUALQUER produto vinculado for
     eletro; vinculado e nenhum eletro = não é (mesmo que a Shopee diga
     Eletrodomésticos). Produto eletro = SKU com tag `eletro`
     (sku_tags.classify_sku_tag), OU categoria do Bling que começa com
     "eletro" ("Eletro", "Eletro Kit"), OU segmento debaixo da raiz `eletro`.
  2. Sem vínculo: a categoria de nível 1 da Shopee (vinda dos itens de
     afiliados de qualquer semana): 100010 Eletrodomésticos e 100636 Casa e
     Decoração são eletro; outra categoria não é.
  3. Sem vínculo e sem categoria: o título (sem diferenciar maiúscula nem
     acento), pela regex `TITULO_ELETRO`.

O caminho até o vínculo: `conferencia_shopee_conta.conta_key` → `store_info`
(Shopee, não arquivada, account_name em minúsculas) → `pricing_accounts`
(`store_info_id` → `integration_id`; é a ÚNICA ponte confiável — o
`store_info.integration_id` está vazio em toda loja Shopee hoje, mas entra
também se um dia for preenchido) → `product_links.integration_id`.
Seis lojas não têm integração Shopee (Injox, Lucas MEI, Luno, Oliveira, VR,
Eron): para elas valem só as regras 2 e 3.

Os três sinais da regra 1 existem nos models (conferido em 06/10/2026):
`products.sku`, `products.category` (+ `product_categories.bling_category_id`
/ `name`) e `products.segment_id` (+ `segments.parent_id` / `slug`).
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    IntegrationPlatform,
    PricingAccount,
    PricingPlatform,
    Product,
    ProductCategory,
    ProductLink,
    Segment,
    StoreInfo,
)
from app.services.sku_tags import classify_sku_tag

# Categorias de nível 1 da Shopee que contam como eletro (regra 2).
CATEGORIAS_ELETRO = frozenset({100010, 100636})

# Regra 3, sobre o título sem acento e em minúsculas.
TITULO_ELETRO = re.compile(
    r"air ?fryer|airfryer|fritadeira|cafeteira|slush|cookware|panela|smart ?cook|sorvet"
)

ORIGENS = ("davinci", "categoria", "titulo", "nenhuma")


def sem_acento(texto: str | None) -> str:
    """Minúsculo e sem acento ("Sorvetéira" → "sorveteira")."""
    nfkd = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).casefold()


def _categoria(valor: object) -> int | None:
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        return None


def categorias_por_item(dados: Mapping | None) -> dict[str, int]:
    """item_id → categoria de nível 1 da Shopee, dos itens de afiliados de
    QUALQUER semana da coleta (a primeira que aparecer)."""
    saida: dict[str, int] = {}
    semanas = (dados or {}).get("semanas")
    for semana in semanas if isinstance(semanas, list) else []:
        itens = semana.get("afiliados_itens") if isinstance(semana, Mapping) else None
        for item in itens if isinstance(itens, list) else []:
            if not isinstance(item, Mapping):
                continue  # executor com defeito: ignora, não derruba o relatório
            item_id = item.get("item_id")
            cat = _categoria(item.get("categoria_id"))
            if item_id is None or cat is None:
                continue
            saida.setdefault(str(item_id), cat)
    return saida


def classificar(
    item_id: str | None,
    nome: str | None,
    categoria_id_por_item: Mapping[str, int],
    mapa_davinci: Mapping[str, bool],
) -> tuple[bool, str]:
    """(é eletro?, de onde saiu a decisão: davinci | categoria | titulo |
    nenhuma). `mapa_davinci` é o da CONTA (item_id → eletro?), só com os
    itens vinculados; `categoria_id_por_item` também é da conta."""
    chave = str(item_id) if item_id is not None else None
    if chave is not None and chave in mapa_davinci:
        return bool(mapa_davinci[chave]), "davinci"
    if chave is not None and chave in categoria_id_por_item:
        return categoria_id_por_item[chave] in CATEGORIAS_ELETRO, "categoria"
    if nome and TITULO_ELETRO.search(sem_acento(nome)):
        return True, "titulo"
    return False, "nenhuma"


def divergencia(
    item_id: str,
    categoria_id_por_item: Mapping[str, int],
    mapa_davinci: Mapping[str, bool],
) -> dict | None:
    """Item vinculado cuja categoria da Shopee discorda do DaVinci (vai para
    as Notas, para conferência). None = concorda ou não dá para comparar."""
    if item_id not in mapa_davinci or item_id not in categoria_id_por_item:
        return None
    cat = categoria_id_por_item[item_id]
    davinci = bool(mapa_davinci[item_id])
    if (cat in CATEGORIAS_ELETRO) == davinci:
        return None
    return {"davinci": "eletro" if davinci else "outro", "categoria_shopee": cat}


# ───────────────────────────────────────────────────────── vínculo do DaVinci


def _chave_conta(valor: str | None) -> str:
    return (valor or "").strip().lower()


async def _integracoes_por_conta(
    session: AsyncSession, chaves: set[str]
) -> dict[str, set[UUID]]:
    lojas = (
        await session.execute(
            select(StoreInfo.id, StoreInfo.account_name, StoreInfo.integration_id).where(
                func.lower(func.btrim(StoreInfo.platform)) == "shopee",
                StoreInfo.archived_at.is_(None),
                func.lower(func.btrim(StoreInfo.account_name)).in_(chaves),
            )
        )
    ).all()
    conta_da_loja: dict[UUID, str] = {}
    saida: dict[str, set[UUID]] = defaultdict(set)
    for loja_id, nome, integracao in lojas:
        chave = _chave_conta(nome)
        conta_da_loja[loja_id] = chave
        if integracao is not None:
            saida[chave].add(integracao)
    if conta_da_loja:
        contas = (
            await session.execute(
                select(PricingAccount.store_info_id, PricingAccount.integration_id).where(
                    PricingAccount.platform == PricingPlatform.SHOPEE,
                    PricingAccount.store_info_id.in_(list(conta_da_loja)),
                    PricingAccount.integration_id.is_not(None),
                )
            )
        ).all()
        for loja_id, integracao in contas:
            saida[conta_da_loja[loja_id]].add(integracao)
    return saida


async def _segmentos_eletro(session: AsyncSession) -> set[UUID]:
    """A raiz `eletro` e todos os descendentes (árvore pequena: lida inteira)."""
    linhas = (await session.execute(select(Segment.id, Segment.parent_id, Segment.slug))).all()
    filhos: dict[UUID, list[UUID]] = defaultdict(list)
    fila: list[UUID] = []
    for seg_id, pai, slug in linhas:
        if pai is None:
            if (slug or "").strip().lower() == "eletro":
                fila.append(seg_id)
        else:
            filhos[pai].append(seg_id)
    vistos: set[UUID] = set()
    while fila:
        seg = fila.pop()
        if seg in vistos:
            continue
        vistos.add(seg)
        fila.extend(filhos.get(seg, ()))
    return vistos


async def _categorias_bling(session: AsyncSession) -> dict[str, str]:
    """id da categoria do Bling (em texto) → nome. `products.category` guarda
    ora o id, ora o próprio nome (como em bling_orders._product_categories_by_item)."""
    linhas = (
        await session.execute(select(ProductCategory.bling_category_id, ProductCategory.name))
    ).all()
    return {str(cat_id): nome or "" for cat_id, nome in linhas}


def _categoria_eletro(categoria: str | None, por_id: Mapping[str, str]) -> bool:
    bruto = (categoria or "").strip()
    if not bruto:
        return False
    nome = por_id.get(bruto)
    if nome is None:
        if bruto.isdigit():
            return False  # id de categoria que o DaVinci não conhece
        nome = bruto
    return nome.strip().lower().startswith("eletro")


def produto_eletro(
    sku: str | None,
    categoria: str | None,
    segment_id: UUID | None,
    categorias_por_id: Mapping[str, str],
    segmentos_eletro: set[UUID],
) -> bool:
    """Um produto do DaVinci é eletro? (SKU, categoria do Bling ou segmento)."""
    return (
        classify_sku_tag(sku) == "eletro"
        or _categoria_eletro(categoria, categorias_por_id)
        or (segment_id is not None and segment_id in segmentos_eletro)
    )


async def carregar_mapa_davinci(
    session: AsyncSession,
    contas_keys: Iterable[str | None],
    item_ids: Iterable[str] | None = None,
) -> dict[str, dict[str, bool]]:
    """Para cada `conta_key`: item_id da Shopee → eletro? (só os itens com
    vínculo vivo no DaVinci). Conta sem loja, sem integração ou sem vínculo
    fica com o mapa vazio — e cai nas regras 2 e 3 de `classificar`.

    `item_ids` (opcional) limita a busca aos itens que as coletas trouxeram."""
    chaves = {_chave_conta(c) for c in contas_keys if _chave_conta(c)}
    saida: dict[str, dict[str, bool]] = {c: {} for c in chaves}
    if not chaves:
        return saida
    integracoes = await _integracoes_por_conta(session, chaves)
    todas = {i for ids in integracoes.values() for i in ids}
    if not todas:
        return saida

    filtros = [
        ProductLink.platform == IntegrationPlatform.SHOPEE,
        ProductLink.morto_desde.is_(None),
        ProductLink.integration_id.in_(list(todas)),
    ]
    if item_ids is not None:
        itens = sorted({str(i) for i in item_ids if i is not None})
        if not itens:
            return saida
        filtros.append(ProductLink.external_id.in_(itens))
    vinculos = (
        await session.execute(
            select(
                ProductLink.integration_id,
                ProductLink.external_id,
                Product.sku,
                Product.category,
                Product.segment_id,
            )
            .join(Product, Product.id == ProductLink.product_id)
            .where(*filtros)
        )
    ).all()
    if not vinculos:
        return saida

    por_id = await _categorias_bling(session)
    segmentos = await _segmentos_eletro(session)
    por_integracao: dict[UUID, dict[str, bool]] = defaultdict(dict)
    for integracao, item_id, sku, categoria, segmento in vinculos:
        eletro = produto_eletro(sku, categoria, segmento, por_id, segmentos)
        mapa = por_integracao[integracao]
        mapa[str(item_id)] = mapa.get(str(item_id), False) or eletro
    for chave, ids in integracoes.items():
        mapa = saida[chave]
        for integracao in ids:
            for item_id, eletro in por_integracao.get(integracao, {}).items():
                mapa[item_id] = mapa.get(item_id, False) or eletro
    return saida
