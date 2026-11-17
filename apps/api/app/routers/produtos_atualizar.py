"""Produtos › botão Atualizar (17/11/2026): POST /api/produtos/atualizar.

Só LÊ do Bling e dos marketplaces e só GRAVA no DaVinci (ver
services/produtos_botoes.py). As contas são montadas no SERVIDOR (o corpo é
vazio): não arquivadas, das plataformas com leitor da lista e, para usuário
de equipe, só as da equipe. Uma massa por vez para TODOS (409 com o que está
rodando, quem disparou e desde quando).
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    IntegrationPlatform,
    User,
)
from app.schemas.products import JobCreatedOut
from app.services.advisory_lock import recusar_se_massa_em_andamento
from app.services.produtos_botoes import PLATAFORMAS_LISTA, contas_do_botao
from app.worker_pool import get_arq_sync_pool

logger = structlog.get_logger()
router = APIRouter(prefix="/api/produtos", tags=["products"])


@router.post(
    "/atualizar", response_model=JobCreatedOut, status_code=status.HTTP_201_CREATED
)
async def atualizar(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("produtos", "edit"))],
) -> JobCreatedOut:
    """Enfileira o Atualizar (job IMPORT_LISTINGS, trigger 'botao_atualizar',
    fila davinci_sync). 409 'sync_already_running' se já há Sincronizar,
    Vincular, Atualizar, a noturna ou a varredura das 10h de QUALQUER pessoa;
    400 'sem_contas' se não há conta com lista no escopo de quem clicou."""
    await recusar_se_massa_em_andamento(session)

    # Todas as contas do escopo (menos o Bling): as com leitor da lista são
    # lidas; as Amazon entram só no Vincular do fim (lidas ao vivo); as outras
    # aparecem como "sem leitura da lista".
    todas = await contas_do_botao(
        session, user, [p for p in IntegrationPlatform if p != IntegrationPlatform.BLING]
    )
    lista = [c for c in todas if c.platform in PLATAFORMAS_LISTA]
    if not lista:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": "sem_contas"})
    amazon = [c for c in todas if c.platform == IntegrationPlatform.AMAZON]
    sem_leitura = sorted({c.platform.value for c in todas if c.platform not in PLATAFORMAS_LISTA})

    ids_txt = [str(c.id) for c in lista]
    job = BackgroundJob(
        type=BackgroundJobType.IMPORT_LISTINGS,
        status=BackgroundJobStatus.PENDING,
        created_by=user.id,
        total=len(ids_txt) + 1,
        payload={
            "trigger": "botao_atualizar",
            "integration_ids": ids_txt,
            "vincular_amazon_ids": [str(c.id) for c in amazon],
            "sem_leitura": sem_leitura,
        },
    )
    session.add(job)
    await session.flush()

    pool = await get_arq_sync_pool()
    arq = await pool.enqueue_job("atualizar_run", str(job.id), ids_txt)
    if arq is not None:
        job.arq_job_id = arq.job_id
    await session.commit()
    logger.info(
        "atualizar_enfileirado", job_id=str(job.id), contas=len(ids_txt), amazon=len(amazon)
    )
    return JobCreatedOut(job_id=job.id)
