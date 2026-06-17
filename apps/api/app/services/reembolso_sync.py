"""Fonte única da replicação `davinci.refunds` -> `bling_orders.reembolso`.

Recalcula `bling_orders.reembolso` de um pedido com a soma (com sinal) dos
reembolsos dos refunds CONFERIDOS daquele pedido. Apenas refunds com
`conferido = true` entram no lucro/margem — um refund vira efetivo só quando o
usuário marca o check. Casa por `numero` (== refunds.pedido_bling) e grava o
MESMO total em todas as linhas (itens) do pedido — a vw_bling_pedidos rateia por
item_proportion, então o valor cheio em cada linha não duplica.

Usada em dois caminhos:
  - página de Reembolso (refunds router): após create/patch/delete de refund.
  - ingest de pedidos (bling_orders.upsert_order): o re-ingest insere linhas
    novas com reembolso=0 (server_default), e o ingest de um pedido NOVO que já
    tinha refund lançado nasceria com 0 — re-aplicar aqui mantém o reembolso.

Recompute-from-scratch: idempotente. NÃO faz commit — o caller controla a
transação.
"""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, Refund


async def sync_reembolso_for_pedido(
    session: AsyncSession, pedido_bling: str | None
) -> None:
    pedido = (pedido_bling or "").strip()
    if not pedido:
        return
    total = (
        await session.execute(
            select(func.coalesce(func.sum(Refund.reembolso), 0.0)).where(
                Refund.pedido_bling == pedido,
                Refund.conferido.is_(True),
            )
        )
    ).scalar_one()
    await session.execute(
        update(BlingOrder)
        .where(
            BlingOrder.bling_id.in_(
                select(BlingOrder.bling_id).where(
                    BlingOrder.numero == pedido,
                    BlingOrder.bling_id.is_not(None),
                )
            )
        )
        .values(reembolso=float(total or 0.0))
    )
