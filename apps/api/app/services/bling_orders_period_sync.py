"""Varredura horária por período: recupera webhooks perdidos do Bling.

O caminho primário para manter `bling_orders` fresco é o webhook
(`pedido.alteracao.situacao`), mas o Bling V3 perde webhooks com
frequência. A safety-net (`bling_orders_safety_net`) só cobre as
situações de envio (`6`, `83965`); transições fora delas — p. ex.
`15` → devolução (`83957`) — nunca se auto-corrigem quando o webhook
se perde (caso real do pedido 277234).

Esta varredura é **situação-agnóstica**: a cada hora lista os pedidos
cuja ÚLTIMA ALTERAÇÃO (`dataAlteracao` no Bling) caiu numa janela
recente e re-ingere cada um via `ingest_bling_order_run` (idempotente).
Pega QUALQUER transição perdida, independente da situação.

Janela de 2h com cron de 1h → cada alteração é vista ≥2x (overlap),
resiliente a clock skew e a alterações na borda do intervalo.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Integration, IntegrationPlatform
from app.services.devolution_stock_return import _get_bling_client

logger = structlog.get_logger()

SAO_PAULO = ZoneInfo("America/Sao_Paulo")

# Janela de varredura (horas). Cron roda de hora em hora; 2h dá overlap.
WINDOW_HOURS = 2

# Teto de segurança por ciclo. Volume real em 2h é dezenas de pedidos; se
# estourar, loga (NÃO silencia) — sinal de backlog/reprocesso em massa.
# 500 → 150 (incidente 30/06): cada re-ingest dispara um refresh do snapshot
# verificar_margem (gargalo serializado, ~10/min). Uma rajada de 500 num único
# tick horário, durante um surto de mudança de status à tarde, afogava o worker
# e empilhava o backlog. 150 ainda cobre o volume normal + surtos moderados;
# excedente é capturado nos ticks seguintes (overlap de WINDOW_HOURS) e logado.
MAX_PER_CYCLE = 150

# Bling exige timestamp completo no filtro de data de alteração.
_BLING_DT_FMT = "%Y-%m-%d %H:%M:%S"


async def find_recently_changed_bling_ids(
    session: AsyncSession,
) -> tuple[list[int], UUID | None]:
    """Lista os `bling_id` dos pedidos alterados nas últimas `WINDOW_HOURS`
    horas (pela `dataAlteracao` do Bling) e o `user_id` da integração Bling
    ativa (single-tenant) — mesmo argumento que o webhook passa para
    `ingest_bling_order_run`.

    Retorna ([], None) quando não há integração Bling; ([], user_id)
    quando há integração mas o client não pôde ser montado.
    """
    integ = (
        await session.execute(
            select(Integration).where(
                Integration.platform == IntegrationPlatform.BLING,
                Integration.status == "active",
            ).limit(1)
        )
    ).scalar_one_or_none()
    if integ is None:
        return [], None
    user_id = integ.user_id

    client = await _get_bling_client(session)
    if client is None:
        return [], user_id

    now_sp = datetime.now(SAO_PAULO)
    start_sp = now_sp - timedelta(hours=WINDOW_HOURS)

    ids: list[int] = []
    seen: set[int] = set()
    try:
        async for pedido in client.iter_pedidos_vendas(
            data_alteracao_inicial=start_sp.strftime(_BLING_DT_FMT),
            data_alteracao_final=now_sp.strftime(_BLING_DT_FMT),
        ):
            raw = pedido.get("id")
            try:
                bid = int(raw)
            except (TypeError, ValueError):
                continue
            if bid in seen:
                continue
            seen.add(bid)
            ids.append(bid)
            if len(ids) >= MAX_PER_CYCLE:
                logger.warning(
                    "bling_orders_period_sync_cap_hit",
                    cap=MAX_PER_CYCLE,
                    window_hours=WINDOW_HOURS,
                )
                break
    finally:
        # Persiste eventual refresh de token feito pelo client durante a
        # paginação (o on_token_refresh do _get_bling_client faz commit,
        # mas garantimos consistência mesmo em caminho de erro).
        await session.commit()

    return ids, user_id
