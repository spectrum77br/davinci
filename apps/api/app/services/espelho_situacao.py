"""Adiantar a situação do pedido no espelho local (`BlingOrder.situacao`) logo
depois de o DaVinci mudá-la no Bling — sem esperar o webhook/sync.

Quem adianta o espelho passa a ser a "situação anterior" que o sync vê quando o
webhook chega (bling_orders.upsert_order). Por isso as datas que o sync carimba
na TRANSIÇÃO — entrada em Aguardando Devolução (`aguardando_devolucao_data`,
aba Acompanhamento) e etiqueta→Atendido (`em_andamento_data`) — são decididas
aqui, com as MESMAS regras do sync e a situação de antes. Sem isso, o sync via
83957→83957 e mantinha a data de um episódio antigo (revisão de 26/10, caso
300978).

Filtra pelo `bling_id` (o `numero` se repete entre anos). Best-effort e num
savepoint: um erro aqui nunca derruba quem chamou (a mudança no Bling já foi).
NÃO commita.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder

logger = structlog.get_logger()


async def adiantar_situacao(
    session: AsyncSession, bling_id: int | str | None, nova: int | str
) -> bool:
    """Grava `nova` no espelho do pedido `bling_id`, com as datas de transição
    que o sync gravaria. Devolve True quando gravou."""
    if bling_id is None:
        return False
    # Import tardio: bling_orders puxa o worker/integrações.
    from app.services.bling_orders import (
        _next_aguardando_devolucao_data,
        _next_em_andamento_data,
    )

    nova = str(nova)
    try:
        async with session.begin_nested():
            linhas = (
                await session.execute(
                    select(
                        BlingOrder.situacao,
                        BlingOrder.em_andamento_data,
                        BlingOrder.aguardando_devolucao_data,
                    ).where(BlingOrder.bling_id == int(bling_id))
                )
            ).all()
            if not linhas:
                return False
            antiga = linhas[0][0]
            agora = datetime.now(UTC)
            valores: dict = {"situacao": nova}
            em_andamento = _next_em_andamento_data(
                nova_situacao=nova,
                situacao_antiga=antiga,
                data_existente=next((r[1] for r in linhas if r[1] is not None), None),
                agora=agora,
            )
            if em_andamento is not None:
                valores["em_andamento_data"] = em_andamento
            devolucao = _next_aguardando_devolucao_data(
                nova_situacao=nova,
                situacao_antiga=antiga,
                data_existente=next((r[2] for r in linhas if r[2] is not None), None),
                agora=agora,
            )
            if devolucao is not None:
                valores["aguardando_devolucao_data"] = devolucao
            await session.execute(
                update(BlingOrder).where(BlingOrder.bling_id == int(bling_id)).values(**valores)
            )
        return True
    except Exception as exc:  # noqa: BLE001 — o sync corrige depois
        logger.warning(
            "espelho_situacao_falhou", bling_id=str(bling_id), situacao=nova, error=str(exc)
        )
        return False
