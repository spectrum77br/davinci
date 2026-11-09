"""Itens do pedido para a declaração de conteúdo (06/11/2026).

Fonte: o espelho do Bling (`bling_orders`), uma linha por item, lida pelas
COLUNAS (`item_codigo`, `item_descricao`, `item_quantidade`, `item_index`) — é
o que o robô de prioridade e o Atendimento atualizam direto na troca; o JSONB
`itens` pode ficar velho no caminho estreito do upsert.

- Só o `bling_id` VIVO de cada número (mesma regra de `_linhas_vivas` em
  `routers/nf.py`): pedido excluído e reimportado não soma os itens da versão
  morta. Linha `situacao='excluido'` e item sem código ficam de fora.
- SEM a cerca de tag (`sku_tags`): a declaração vai inteira na caixa, mesmo
  quando quem imprime é o operador cercado num armazém.
- Pedido fake (`item_codigo` começando com "fake", ex. 'fake.fake') é de
  propósito: devolve [] e a declaração do marketplace fica como veio.

Lista vazia = não mexer na declaração (`reescrever_declaracao` → sem_itens).

Os endpoints (subida e impressão) chamam `itens_para_declaracao`, que NUNCA
levanta: a consulta roda num SAVEPOINT e qualquer falha vira "sem itens" (a
etiqueta sobe/imprime com a declaração como está) — esta regra não pode
derrubar uma impressão ou uma subida que funcionavam sem ela.

Os mesmos itens alimentam o rodapé de picking da Shopee (09/11/2026,
`nf_etiqueta_picking`). `pedidos_da_shopee` diz, na impressão, quais pedidos
são da Shopee (só eles ganham rodapé quando o blob guardado não tem) — com a
mesma regra de nunca levantar.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, StoreInfo
from app.services.nf_etiqueta_declaracao import ItemDeclaracao, formatar_item
from app.services.nf_etiqueta_picking import parece_pedido_shopee

logger = structlog.get_logger()

_ANTIGO = datetime.min.replace(tzinfo=UTC)


def _e_fake(sku: str | None) -> bool:
    return (sku or "").strip().lower().startswith("fake")


async def itens_dos_pedidos(
    session: AsyncSession, numeros: Iterable[str | None]
) -> dict[str, list[ItemDeclaracao]]:
    """numero → itens da declaração, na ordem do Bling (`item_index`). Uma
    consulta só para todos os números (impressão em lote, subida em lote).
    Número sem espelho não aparece no dict."""
    nums = [n for n in dict.fromkeys(numeros) if n]
    if not nums:
        return {}
    rows = (
        await session.execute(
            select(
                BlingOrder.numero,
                BlingOrder.bling_id,
                BlingOrder.updated_at,
                BlingOrder.item_index,
                BlingOrder.item_codigo,
                BlingOrder.item_descricao,
                BlingOrder.item_quantidade,
            ).where(
                BlingOrder.numero.in_(nums),
                BlingOrder.situacao.is_distinct_from("excluido"),
            )
        )
    ).all()
    por_numero: dict[str, list] = {}
    for r in rows:
        por_numero.setdefault(r.numero, []).append(r)
    out: dict[str, list[ItemDeclaracao]] = {}
    for numero, rs in por_numero.items():
        dono = max(rs, key=lambda r: (int(r.bling_id or 0), r.updated_at or _ANTIGO))
        vivos = sorted(
            (r for r in rs if r.bling_id == dono.bling_id),
            key=lambda r: (r.item_index or 0, r.item_codigo or ""),
        )
        if any(_e_fake(r.item_codigo) for r in vivos):
            out[numero] = []
            continue
        vistos: set[tuple] = set()
        itens: list[ItemDeclaracao] = []
        for r in vivos:
            chave = (r.item_index, r.item_codigo)
            if chave in vistos or not (r.item_codigo or "").strip():
                continue
            vistos.add(chave)
            itens.append(formatar_item(r.item_codigo, r.item_descricao, r.item_quantidade))
        out[numero] = itens
    return out


async def itens_do_pedido(session: AsyncSession, numero: str | None) -> list[ItemDeclaracao]:
    """Atalho de `itens_dos_pedidos` para um número."""
    if not numero:
        return []
    return (await itens_dos_pedidos(session, [numero])).get(numero, [])


async def pedidos_da_shopee(
    session: AsyncSession, numeros: Iterable[str | None], *, origem: str
) -> set[str]:
    """Números (de `numeros`) cujo pedido é da Shopee — só eles ganham o rodapé
    de picking na impressão quando o blob guardado não tem (etiquetas subidas
    entre 04/08 e 09/11/2026, quando o transform só apagava).

    Pela loja do pedido: `bling_orders.loja` → `store_info.bling_store_id` →
    `platform` (mesma ligação do lease do executor). Loja que não está em
    `store_info`: pelo formato do nº da Shopee (`parece_pedido_shopee`). Uma
    consulta só, num SAVEPOINT; NUNCA levanta — na falha devolve vazio (a
    etiqueta antiga imprime sem rodapé, como hoje) e loga warning."""
    nums = [n for n in dict.fromkeys(numeros) if n]
    if not nums:
        return set()
    try:
        async with session.begin_nested():
            rows = (
                await session.execute(
                    select(
                        BlingOrder.numero,
                        BlingOrder.numeroloja,
                        func.lower(func.trim(StoreInfo.platform)).label("plataforma"),
                    )
                    .select_from(BlingOrder)
                    .join(StoreInfo, StoreInfo.bling_store_id == BlingOrder.loja, isouter=True)
                    .where(
                        BlingOrder.numero.in_(nums),
                        BlingOrder.situacao.is_distinct_from("excluido"),
                    )
                )
            ).all()
    except Exception as exc:  # noqa: BLE001 — o rodapé nunca trava a impressão
        logger.warning(
            "nf_etiqueta_picking_plataforma_falhou",
            origem=origem,
            pedidos=nums[:20],
            total=len(nums),
            erro=f"{type(exc).__name__}: {exc}"[:300],
        )
        return set()
    plataformas: dict[str, set[str]] = {}
    numerolojas: dict[str, set[str]] = {}
    for r in rows:
        if r.plataforma:
            plataformas.setdefault(r.numero, set()).add(r.plataforma)
        if r.numeroloja:
            numerolojas.setdefault(r.numero, set()).add(r.numeroloja)
    out: set[str] = set()
    for n in nums:
        plats = plataformas.get(n)
        if plats:
            if "shopee" in plats:
                out.add(n)
        elif any(parece_pedido_shopee(x) for x in numerolojas.get(n, ())):
            out.add(n)
    return out


async def itens_para_declaracao(
    session: AsyncSession, numeros: Iterable[str | None], *, origem: str
) -> dict[str, list[ItemDeclaracao]]:
    """`itens_dos_pedidos` para os endpoints: NUNCA levanta.

    A consulta roda num SAVEPOINT: um erro do banco nela (timeout da consulta,
    erro de SQL) aborta só o savepoint, e a transação de quem chamou continua
    servindo o carimbo `impressa_em` da impressão e o upsert da subida. Na
    falha devolve {} — a declaração fica como está, como num pedido sem
    espelho — e loga warning. (Conexão caída derruba o resto do endpoint de
    qualquer jeito, com ou sem esta regra.)"""
    nums = [n for n in dict.fromkeys(numeros) if n]
    if not nums:
        return {}
    try:
        async with session.begin_nested():
            return await itens_dos_pedidos(session, nums)
    except Exception as exc:  # noqa: BLE001 — a declaração nunca trava o fluxo
        logger.warning(
            "nf_etiqueta_declaracao_itens_falhou",
            origem=origem,
            pedidos=nums[:20],
            total=len(nums),
            erro=f"{type(exc).__name__}: {exc}"[:300],
        )
        return {}
