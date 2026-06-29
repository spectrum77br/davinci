"""Rede de segurança DIÁRIA por data de emissão: recupera pedidos que
nunca foram ingeridos.

As outras duas redes têm um vão em comum — nenhuma descobre um pedido
totalmente AUSENTE do banco:

  - `bling_orders_safety_net` (10min) só dá refresh em linha que JÁ existe
    (varre `bling_orders` procurando stale); não enxerga o que falta.
  - `bling_orders_period_sync` (1h) lista por `dataAlteracao` do Bling, e
    fica CEGA quando o Bling devolve `dataAlteracao=None` (caso comum em
    pedido recém-criado).

Quando o `ingest_bling_order_run` falha (Bling 500 ou transação Postgres
envenenada) e esgota os retries do arq, o pedido nunca entra — e ninguém
o recupera. Foi o que sumiu ~3,4% dos pedidos em 28-29/06/2026.

Esta varredura fecha o vão: lista TODOS os pedidos do Bling por data de
EMISSÃO (`data`, não `dataAlteracao`) numa janela curta e compara com o
banco pelo `numero`:

  - `numero` ausente no banco  → ingere (INSERT do pedido perdido).
  - `numero` existe mas a situação no Bling ≠ situação no banco → ingere
    (UPDATE — capta uma transição cuja notificação se perdeu).
  - `numero` existe e situação igual → PULA (não re-toca; nada de
    re-insert nem re-stamp desnecessário).

O `upsert_order` chamado pelo ingest já é idempotente (UPSERT por
`(bling_id, item_index)`, nunca DELETE+INSERT), então o caminho de UPDATE
preserva `id`, `reembolso`, `em_andamento_data`, `aprovado_por`, etc.

Roda 1×/dia. A janela de `WINDOW_DAYS` cobre o dia anterior inteiro
independentemente da hora do cron.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, Integration, IntegrationPlatform
from app.services.devolution_stock_return import _get_bling_client

logger = structlog.get_logger()

SAO_PAULO = ZoneInfo("America/Sao_Paulo")

# Janela de varredura por data de EMISSÃO (dias). 2 = hoje + ontem, de modo
# que rodando de manhã (BRT) o dia anterior já está completo.
WINDOW_DAYS = 2

# Teto de pedidos a re-enfileirar por execução. Em regime normal o delta
# diário é de poucos pedidos (só os que falharam); se estourar, loga (NÃO
# silencia) — sinal de incidente em massa.
MAX_PER_CYCLE = 500


def _situacao_id(it: dict) -> str | None:
    sit = it.get("situacao")
    if isinstance(sit, dict):
        sid = sit.get("id")
        return str(sid) if sid is not None else None
    return str(sit) if sit is not None else None


async def find_daily_backfill_candidates(
    session: AsyncSession,
) -> tuple[list[int], UUID | None]:
    """Lista os `bling_id` dos pedidos da janela que estão AUSENTES do banco
    ou cuja situação divergiu, e o `user_id` da integração Bling ativa.

    Retorna ([], None) quando não há integração Bling; ([], user_id) quando
    há integração mas o client não pôde ser montado.
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

    today_sp: date = datetime.now(SAO_PAULO).date()
    data_inicial = (today_sp - timedelta(days=WINDOW_DAYS - 1)).isoformat()
    data_final = today_sp.isoformat()

    # numero (str) -> (bling_id, situacao_id) do Bling, por data de emissão.
    bling_by_numero: dict[str, tuple[int, str | None]] = {}
    async for pedido in client.iter_pedidos_vendas(
        data_inicial=data_inicial, data_final=data_final
    ):
        numero = pedido.get("numero")
        bling_id = pedido.get("id")
        if numero is None or bling_id is None:
            continue
        bling_by_numero[str(numero)] = (int(bling_id), _situacao_id(pedido))

    if not bling_by_numero:
        return [], user_id

    # Situação atual no banco por numero (uma situação por pedido).
    db_rows = (
        await session.execute(
            select(BlingOrder.numero, BlingOrder.situacao).where(
                BlingOrder.numero.in_(list(bling_by_numero.keys()))
            )
        )
    ).all()
    db_situacao_by_numero: dict[str, str | None] = {}
    for numero, situacao in db_rows:
        db_situacao_by_numero.setdefault(numero, situacao)

    candidates: list[int] = []
    missing = 0
    changed = 0
    for numero, (bling_id, bling_sit) in bling_by_numero.items():
        if numero not in db_situacao_by_numero:
            candidates.append(bling_id)
            missing += 1
        elif db_situacao_by_numero[numero] != bling_sit:
            candidates.append(bling_id)
            changed += 1
        # senão: presente e inalterado → não re-toca.

    logger.info(
        "bling_orders_daily_backfill_scan",
        window=f"{data_inicial}..{data_final}",
        bling_total=len(bling_by_numero),
        missing=missing,
        changed=changed,
    )

    if len(candidates) > MAX_PER_CYCLE:
        logger.warning(
            "bling_orders_daily_backfill_capped",
            found=len(candidates),
            cap=MAX_PER_CYCLE,
        )
        candidates = candidates[:MAX_PER_CYCLE]

    return candidates, user_id
