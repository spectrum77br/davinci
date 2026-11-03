"""Conferência de preços da Tabela de Preços (02/11/2026) — só administrador.

Eduardo, tarefa "Melhorias na Tabela de Preços": botão com "% margem de erro"
que compara o preço da Tabela com o preço do anúncio em cada conta e marca
(!) quando passa da margem. O clique enfileira o job do departamento
(`services/pricing/conferencia.py`); a tela acompanha pelo GET, que devolve o
último job e o que a última conferência gravou em cada célula. A margem é
aplicada na tela: mudar a % não confere de novo.

Cada célula vem com o job que a gravou e a resposta traz o último job que
terminou bem (`ultimo_concluido`): na tela vale a leitura do último job ou
desse; a de uma conferência que parou no meio, ou da conta que não conseguiu
gravar, aparece como não conferida (?).

  POST /api/pricing/conferencia   {"department": "celular"} → 201 {"job_id"}
  GET  /api/pricing/conferencia?department=celular

Job pendente há mais de 10 min que o arq diz que não vai rodar (o worker_sync
no código velho descarta o job com "function not found"): o GET e o clique o
dão como falho ('worker_nao_executou', gravado no job) em vez de esperar as
2 h do PENDENTE_ESQUECIDO. Sem resposta do Redis, vale a regra de sempre.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import worker_pool
from app.db import get_session
from app.deps.auth import require_admin
from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    PricingConferencia,
    User,
)
from app.routers.pricing import _resolve_root_segment_id
from app.schemas.products import JobCreatedOut
from app.services.pricing import conferencia

logger = structlog.get_logger()
router = APIRouter(prefix="/api/pricing", tags=["pricing"])

# O job da fila de sync em massa (WorkerSettingsSync).
NOME_DO_JOB = "conferir_precos_run"
# Job pendente/rodando que parou de dar sinal: a tela recebe "failed" para não
# esperar para sempre (o banco fica como está; o próximo clique já é aceito).
ERRO_SEM_SINAL = "parou_sem_sinal"


class ConferenciaIn(BaseModel):
    department: str


class ConferenciaJobOut(BaseModel):
    id: UUID
    status: str
    total: int
    processed: int
    created_at: datetime
    finished_at: datetime | None = None
    error: str | None = None
    result: dict[str, Any]


class ConferenciaCelulaOut(BaseModel):
    pricing_account_id: UUID
    pricing_product_id: UUID
    preco_tabela: float | None = None
    situacao: str
    motivo: str | None = None
    conferido_em: datetime
    anuncios: list[dict[str, Any]]
    job_id: UUID | None = None


class ConferenciaOut(BaseModel):
    job: ConferenciaJobOut | None = None
    ultimo_concluido: UUID | None = None
    celulas: list[ConferenciaCelulaOut]


async def _exigir_departamento(session: AsyncSession, department: str) -> None:
    if await _resolve_root_segment_id(session, department) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": "invalid_department"})


def _job_out(job: BackgroundJob | None) -> ConferenciaJobOut | None:
    if job is None:
        return None
    situacao = job.status.value
    erro = job.error
    em_andamento = job.status in (BackgroundJobStatus.PENDING, BackgroundJobStatus.RUNNING)
    if em_andamento and not conferencia.job_vivo(job):
        situacao, erro = BackgroundJobStatus.FAILED.value, ERRO_SEM_SINAL
    return ConferenciaJobOut(
        id=job.id,
        status=situacao,
        total=job.total,
        processed=job.processed,
        created_at=job.created_at,
        finished_at=job.finished_at,
        error=erro,
        result=job.result or {},
    )


@router.post(
    "/conferencia",
    response_model=JobCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
async def iniciar_conferencia(
    body: ConferenciaIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_admin)],
) -> JobCreatedOut:
    department = body.department
    await _exigir_departamento(session, department)
    if not conferencia.conferencia_liberada():
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "so_no_servidor"})
    await conferencia.travar_departamento(session, department)
    # A pendente que o worker descartou sem rodar vira "failed" aqui mesmo (vai
    # no mesmo commit do job novo) e não segura o clique.
    andamento = await conferencia.job_em_andamento(session, department, perguntar_ao_arq=True)
    if andamento is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "conferencia_em_andamento", "job_id": str(andamento.id)},
        )
    job = BackgroundJob(
        type=BackgroundJobType.CONFERIR_PRECOS,
        status=BackgroundJobStatus.PENDING,
        created_by=user.id,
        payload={"department": department},
    )
    session.add(job)
    # Commit ANTES de enfileirar: o worker pode pegar o job na hora.
    await session.commit()
    try:
        pool = await worker_pool.get_arq_sync_pool()
        arq = await pool.enqueue_job(NOME_DO_JOB, str(job.id), department, str(user.id))
    except Exception as e:
        logger.exception("conferencia_enfileirar_falhou", job_id=str(job.id))
        job.status = BackgroundJobStatus.FAILED
        job.error = "fila_indisponivel"
        job.finished_at = datetime.now(UTC)
        await session.commit()
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail={"code": "fila_indisponivel"}
        ) from e
    if arq is not None:
        job.arq_job_id = arq.job_id
        await session.commit()
    logger.info("conferencia_enfileirada", job_id=str(job.id), department=department)
    return JobCreatedOut(job_id=job.id)


@router.get("/conferencia", response_model=ConferenciaOut)
async def ver_conferencia(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_admin)],
    department: Annotated[str, Query()],
) -> ConferenciaOut:
    await _exigir_departamento(session, department)
    job = await conferencia.ultimo_job(session, department)
    if job is not None and await conferencia.baixar_se_nao_executou(session, job):
        # Gravado: o andamento que a tela consulta (/api/jobs/{id}/status) e o
        # aviso de job falho passam a dizer o mesmo.
        await session.commit()
    concluido = await conferencia.ultimo_job(session, department, so_concluido=True)
    linhas = (
        (
            await session.execute(
                select(PricingConferencia).where(PricingConferencia.department == department)
            )
        )
        .scalars()
        .all()
    )
    return ConferenciaOut(
        job=_job_out(job),
        ultimo_concluido=concluido.id if concluido else None,
        celulas=[
            ConferenciaCelulaOut(
                pricing_account_id=c.pricing_account_id,
                pricing_product_id=c.pricing_product_id,
                preco_tabela=float(c.preco_tabela) if c.preco_tabela is not None else None,
                situacao=c.situacao,
                motivo=c.motivo,
                conferido_em=c.conferido_em,
                anuncios=c.anuncios or [],
                job_id=c.job_id,
            )
            for c in linhas
        ],
    )
