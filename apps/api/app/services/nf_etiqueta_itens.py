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
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder
from app.services.nf_etiqueta_declaracao import ItemDeclaracao, formatar_item

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
