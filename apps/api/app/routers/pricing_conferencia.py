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

M-2/M-3, 17/11/2026 (Eduardo): a margem saiu do navegador e mora no servidor
(a Geral por departamento e a de cada produto, migration 0499), e o cartão
vermelho e o "(!) anúncio X" aparecem para TODAS as pessoas que veem a
Tabela. Por isso o GET deixou de ser só do administrador: vale para quem tem
`tabela_precos` (ver) e devolve só as células das contas que a pessoa veria na
grade (a cerca de equipe do `/grid`, `_contas_da_grade`), mais a margem e se a
pessoa pode conferir. Conferir e mudar a margem continuam só do administrador.

  PUT /api/pricing/conferencia/margem                {"department", "margem_pct"?,
                                                      "geral_ligada"?, "por_produto_ligada"?}
  PUT /api/pricing/conferencia/margem/produto/{id}   {"margem_pct": n | null}
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import worker_pool
from app.db import get_session
from app.deps.auth import require_admin, require_permission
from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    PricingConferencia,
    PricingConferenciaMargem,
    PricingConferenciaMargemProduto,
    PricingProduct,
    Segment,
    User,
    UserRole,
)
from app.models.pricing_conferencia import MARGEM_PADRAO_PCT
from app.routers.pricing import _contas_da_grade, _resolve_root_segment_id
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


class MargemOut(BaseModel):
    """A margem de erro do departamento (M-2, 17/11/2026). Vale, para cada
    linha: com "Por produto" ligada e % própria, a % do produto; senão, com
    "Geral" ligada, a Geral; senão a linha não é conferida."""

    department: str
    margem_pct: float
    geral_ligada: bool
    por_produto_ligada: bool
    # {pricing_product_id: %} — só as linhas da Tabela deste departamento.
    produtos: dict[str, float]
    # Só o administrador muda a margem (a mesma trava de conferir).
    pode_editar: bool


class MargemIn(BaseModel):
    """Campo ausente (ou null) não muda."""

    department: str
    margem_pct: Decimal | None = Field(default=None, ge=0, le=100)
    geral_ligada: bool | None = None
    por_produto_ligada: bool | None = None


class MargemProdutoIn(BaseModel):
    """null apaga a margem própria: o produto volta a usar a Geral."""

    margem_pct: Decimal | None = Field(..., ge=0, le=100)


class ConferenciaOut(BaseModel):
    job: ConferenciaJobOut | None = None
    ultimo_concluido: UUID | None = None
    celulas: list[ConferenciaCelulaOut]
    margem: MargemOut
    # Só o administrador aperta "Conferir anúncios" (POST continua require_admin).
    pode_conferir: bool


def _e_admin(user: User) -> bool:
    return user.role == UserRole.ADMIN


async def _exigir_departamento(session: AsyncSession, department: str) -> UUID:
    """O segmento-raiz da aba; departamento que não existe → 400."""
    root_id = await _resolve_root_segment_id(session, department)
    if root_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": "invalid_department"})
    return root_id


async def _margem_out(
    session: AsyncSession, department: str, root_id: UUID, user: User
) -> MargemOut:
    """A margem gravada do departamento; sem linha, 5% com as duas caixas
    ligadas. `produtos` traz só as linhas da Tabela do departamento (as de
    um tipo — folha — debaixo da raiz, as mesmas que a grade mostra)."""
    geral = (
        await session.execute(
            select(PricingConferenciaMargem)
            .where(PricingConferenciaMargem.department == department)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    produtos = (
        await session.execute(
            select(
                PricingConferenciaMargemProduto.pricing_product_id,
                PricingConferenciaMargemProduto.margem_pct,
            )
            .join(
                PricingProduct,
                PricingProduct.id == PricingConferenciaMargemProduto.pricing_product_id,
            )
            .join(Segment, Segment.id == PricingProduct.segment_id)
            .where(Segment.parent_id == root_id)
        )
    ).all()
    return MargemOut(
        department=department,
        margem_pct=float(geral.margem_pct if geral is not None else MARGEM_PADRAO_PCT),
        geral_ligada=geral.geral_ligada if geral is not None else True,
        por_produto_ligada=geral.por_produto_ligada if geral is not None else True,
        produtos={str(pid): float(pct) for pid, pct in produtos},
        pode_editar=_e_admin(user),
    )


async def _departamento_da_linha(
    session: AsyncSession, prod: PricingProduct
) -> tuple[str, UUID] | None:
    """(slug, raiz) da aba em que a linha aparece na grade: a linha mora num
    tipo (folha) cujo pai é a raiz do departamento. Fora disso, nenhuma."""
    folha = await session.get(Segment, prod.segment_id)
    if folha is None or folha.parent_id is None:
        return None
    raiz = await session.get(Segment, folha.parent_id)
    if raiz is None or raiz.parent_id is not None:
        return None
    return raiz.slug, raiz.id


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
    user: Annotated[User, Depends(require_permission("tabela_precos", "view"))],
    department: Annotated[str, Query()],
) -> ConferenciaOut:
    root_id = await _exigir_departamento(session, department)
    admin = _e_admin(user)
    job = await conferencia.ultimo_job(session, department)
    if job is not None and await conferencia.baixar_se_nao_executou(session, job):
        # Gravado: o andamento que a tela consulta (/api/jobs/{id}/status) e o
        # aviso de job falho passam a dizer o mesmo.
        await session.commit()
    concluido = await conferencia.ultimo_job(session, department, so_concluido=True)
    stmt = select(PricingConferencia).where(PricingConferencia.department == department)
    if not admin:
        # M-3, 17/11/2026: quem não é administrador só recebe as células das
        # contas que veria na grade (equipe; a coluna de catálogo vai com a
        # base). O administrador continua recebendo tudo.
        visiveis = [c.id for c in await _contas_da_grade(session, user, root_id)]
        stmt = stmt.where(PricingConferencia.pricing_account_id.in_(visiveis))
    linhas = (await session.execute(stmt)).scalars().all()
    job_out = _job_out(job)
    if job_out is not None and not admin:
        # O resumo do job (`contas_com_erro` com o nome das integrações, os
        # totais do departamento inteiro) só serve ao balão do botão
        # "Conferir anúncios", que é do administrador: não sai da cerca.
        job_out.result = {}
    return ConferenciaOut(
        job=job_out,
        ultimo_concluido=concluido.id if concluido else None,
        margem=await _margem_out(session, department, root_id, user),
        pode_conferir=admin,
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


@router.put("/conferencia/margem", response_model=MargemOut)
async def gravar_margem(
    body: MargemIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_admin)],
) -> MargemOut:
    """A Geral do departamento e as duas caixas do menu "Margem ▾" (M-2,
    17/11/2026). Só o administrador, a mesma trava de conferir."""
    root_id = await _exigir_departamento(session, body.department)
    mudancas = {
        campo: valor
        for campo, valor in body.model_dump(exclude={"department"}).items()
        if valor is not None
    }
    stmt = pg_insert(PricingConferenciaMargem).values(
        department=body.department, atualizado_por=user.id, **mudancas
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[PricingConferenciaMargem.department],
        set_={**mudancas, "atualizado_por": user.id, "atualizado_em": func.now()},
    )
    await session.execute(stmt)
    await session.commit()
    logger.info(
        "conferencia_margem",
        department=body.department,
        campos=sorted(mudancas),
        user_id=str(user.id),
    )
    return await _margem_out(session, body.department, root_id, user)


@router.put("/conferencia/margem/produto/{pricing_product_id}", response_model=MargemOut)
async def gravar_margem_do_produto(
    pricing_product_id: UUID,
    body: MargemProdutoIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_admin)],
) -> MargemOut:
    """A margem própria de uma linha da Tabela (M-2, 17/11/2026); null apaga
    e a linha volta para a Geral. Responde a margem do departamento da linha."""
    prod = await session.get(PricingProduct, pricing_product_id)
    if prod is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "product_not_found"})
    aba = await _departamento_da_linha(session, prod)
    if aba is None:
        # A linha não aparece em aba nenhuma da grade: não há onde valer.
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail={"code": "produto_sem_departamento"}
        )
    department, root_id = aba
    if body.margem_pct is None:
        await session.execute(
            delete(PricingConferenciaMargemProduto).where(
                PricingConferenciaMargemProduto.pricing_product_id == pricing_product_id
            )
        )
    else:
        stmt = pg_insert(PricingConferenciaMargemProduto).values(
            pricing_product_id=pricing_product_id,
            margem_pct=body.margem_pct,
            atualizado_por=user.id,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[PricingConferenciaMargemProduto.pricing_product_id],
            set_={
                "margem_pct": body.margem_pct,
                "atualizado_por": user.id,
                "atualizado_em": func.now(),
            },
        )
        await session.execute(stmt)
    await session.commit()
    logger.info(
        "conferencia_margem_produto",
        department=department,
        pricing_product_id=str(pricing_product_id),
        margem_pct=None if body.margem_pct is None else str(body.margem_pct),
        user_id=str(user.id),
    )
    return await _margem_out(session, department, root_id, user)
