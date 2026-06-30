"""Safety-net pra webhooks perdidos do Bling: re-sincroniza pedidos
suspeitos de stale a cada 10 minutos.

Bling V3 perde webhooks com frequência — pedidos ficam num estado
intermediário no DB (situacao 6 ou 83965 sem em_andamento_data)
enquanto no Bling já avançaram. Este módulo lista os candidatos; o
cron (`bling_orders_safety_net_tick` em worker.py) força o refetch via
`ingest_bling_order_run`, que é idempotente.

Cobre duas classes de stale:
  1. Envio (6/83965) sem em_andamento_data — avanço perdido cedo.
  2. "Em andamento" (15) parado há muito tempo — o webhook 15→Entregue
     se perdeu e NADA mais reconferia a 15, então pedidos já entregues
     ficavam eternamente como "em rota" e o Faturamento (que conta só
     Entregue) ficava ABAIXO do Bling. Esta varredura corrige isso.

Complementa (não substitui) o `check_marketplace_shipped_orders`:
aquele confronta 83965 contra a API do marketplace; este re-busca o
estado real direto do Bling, pegando também os 6→83965 perdidos.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy import Date, and_, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, Integration, IntegrationPlatform

logger = structlog.get_logger()

# Situações intermediárias do fluxo de envio. Com em_andamento_data NULL
# são candidatos a estarem stale (webhook do Bling pode ter se perdido).
_STALE_CANDIDATE_SITUACOES = ("6", "83965")

# Pedidos travados em "Em andamento" (15): reconferimos os parados entre
# MIN e MAX dias (pela em_andamento_data). <MIN ainda pode estar em rota
# de verdade; >MAX é zumbi (extravio/devolução não-resolvida) que não
# vale ressuscitar a cada ciclo.
_SITUACAO_EM_ANDAMENTO = "15"
_EM_ANDAMENTO_MIN_DAYS = 7
_EM_ANDAMENTO_MAX_DAYS = 60

# Limite por ciclo pra não estourar rate limit do Bling (~3 req/s).
# 30 → 20 (incidente 30/06): cada refetch dispara um refresh do snapshot
# verificar_margem (gargalo serializado). A cada 10 min, 30/ciclo = 180/h só
# desta rede consumiam ~30% da vazão de refresh; 20 alivia o regime contínuo.
_MAX_PER_CYCLE = 20

# Idade mínima da última sync local pra considerar candidato — evita
# disputa com webhooks recém-chegados.
_MIN_STALENESS_MINUTES = 15

# Pedidos criados há mais que isso = zumbis (entregues/cancelados sem
# atualizar). Não vale a pena ressuscitar. (Só vale pro branch de envio;
# o branch "15" usa sua própria janela em_andamento_data.)
_MAX_AGE_DAYS = 14


async def find_stale_order_ids(session: AsyncSession) -> list[tuple[int, UUID]]:
    """Retorna (bling_id, user_id) de pedidos suspeitos de estarem stale.

    Single-tenant: usa o user_id da única Integration BLING ativa — é o
    mesmo argumento que o webhook passa pro `ingest_bling_order_run`.
    Filtra item_index==0 (row canônica), então cada pedido aparece 1x.
    """
    now = datetime.now(UTC)
    cutoff_age = now.date() - timedelta(days=_MAX_AGE_DAYS)
    cutoff_staleness = now - timedelta(minutes=_MIN_STALENESS_MINUTES)

    integ = (await session.execute(
        select(Integration).where(
            Integration.platform == IntegrationPlatform.BLING,
            Integration.status == "active",
        ).limit(1)
    )).scalar_one_or_none()
    if integ is None:
        return []

    # Branch 1: envio (6/83965) sem em_andamento_data — avanço perdido.
    # Teto de 14d na DATA do pedido (zumbis velhos não valem refetch).
    stale_envio = and_(
        BlingOrder.situacao.in_(_STALE_CANDIDATE_SITUACOES),
        BlingOrder.em_andamento_data.is_(None),
        cast(BlingOrder.data, Date) >= cutoff_age,
    )
    # Branch 2: travado em "Em andamento" (15) entre MIN e MAX dias
    # (pela em_andamento_data — há quanto tempo está "em rota").
    em_andamento_recente = now.date() - timedelta(days=_EM_ANDAMENTO_MIN_DAYS)
    em_andamento_antigo = now.date() - timedelta(days=_EM_ANDAMENTO_MAX_DAYS)
    stale_em_andamento = and_(
        BlingOrder.situacao == _SITUACAO_EM_ANDAMENTO,
        BlingOrder.em_andamento_data.isnot(None),
        BlingOrder.em_andamento_data <= em_andamento_recente,
        BlingOrder.em_andamento_data >= em_andamento_antigo,
    )

    rows = (await session.execute(
        select(BlingOrder.bling_id)
        .where(and_(
            or_(stale_envio, stale_em_andamento),
            BlingOrder.updated_at < cutoff_staleness,
            BlingOrder.bling_id.isnot(None),
            BlingOrder.item_index == 0,
        ))
        .order_by(BlingOrder.updated_at.asc())
        .limit(_MAX_PER_CYCLE)
    )).all()

    return [(int(r[0]), integ.user_id) for r in rows]
