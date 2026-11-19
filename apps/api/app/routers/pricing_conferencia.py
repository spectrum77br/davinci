"""Conferência de preços da Tabela de Preços (02/11/2026).

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

M-7, 18/11/2026 (Eduardo): conferir passou para todos que veem a Tabela (o
mesmo `tabela_precos` ver do GET); mudar a margem continua só do
administrador. Quem não é administrador vê no botão a data e o andamento do
último job do departamento (de quem quer que tenha iniciado), sem o resumo
do departamento inteiro (`result`), e o job da conferência não aparece para
ele no /api/jobs (lá o resumo iria inteiro). A tela consulta a cada 30 s com
`celulas=false` (só o job, resposta leve) e relê as células quando uma
conferência termina.

  PUT /api/pricing/conferencia/margem                {"department", "margem_pct"?,
                                                      "geral_ligada"?, "por_produto_ligada"?}
  PUT /api/pricing/conferencia/margem/produto/{id}   {"margem_pct": n | null}

M-14, 18/11/2026: o botão "Atualizar ▾" da Tabela confere só as contas
escolhidas: o POST aceita `contas` (ids das colunas, todas do departamento;
outra → 400 'contas_invalidas'). Elas vão no payload do job e só as células
delas são relidas e limpas — as das outras contas ficam como estavam. Sem
`contas`, o departamento inteiro, como antes. Revisão M-14: para quem é de
equipe, `contas` só entre as colunas que vê na grade, e sem `contas` vão
todas elas (nunca as de outra equipe). O job da parcial vem com `parcial`
(quantas contas) e leva adiante o aviso das contas que não releu.

M-15, 18/11/2026: clicar no cartão vermelho confere só aquela célula. Mesma
permissão do "Conferir anúncios" (`tabela_precos` ver) e só nas colunas que a
pessoa vê na grade (`_contas_da_grade`). Lê na hora (sem job) só os anúncios
vinculados da célula e devolve a célula como ficou gravada (`job_id` null);
não mexe no job nem na data da última conferência do departamento. Leitura
que falhou → 200 com `erro` e nada gravado (o cartão continua).

  POST /api/pricing/conferencia/celula   {"pricing_account_id", "pricing_product_id"}
                                         → 200 {"celula": {...} | null, "erro": str | null}
"""

from __future__ import annotations

from collections.abc import Sequence
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
    PricingAccount,
    PricingConferencia,
    PricingConferenciaMargem,
    PricingConferenciaMargemProduto,
    PricingProduct,
    Segment,
    User,
    UserRole,
)
from app.models.pricing_conferencia import MARGEM_PADRAO_PCT
from app.routers.pricing import _contas_da_grade, _escopo_precos, _resolve_root_segment_id
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
    # M-14: só estas colunas (pricing_accounts do departamento); None = todas.
    contas: Annotated[list[UUID], Field(min_length=1, max_length=1000)] | None = None


class ConferenciaJobOut(BaseModel):
    id: UUID
    status: str
    total: int
    processed: int
    created_at: datetime
    finished_at: datetime | None = None
    error: str | None = None
    result: dict[str, Any]
    # Revisão M-14: conferência só de algumas contas → quantas; None = inteira.
    parcial: int | None = None


class ConferenciaCelulaOut(BaseModel):
    pricing_account_id: UUID
    pricing_product_id: UUID
    preco_tabela: float | None = None
    situacao: str
    motivo: str | None = None
    conferido_em: datetime
    anuncios: list[dict[str, Any]]
    job_id: UUID | None = None


class ConferirCelulaIn(BaseModel):
    pricing_account_id: UUID
    pricing_product_id: UUID


class ConferirCelulaOut(BaseModel):
    """M-15: a célula como ficou gravada; null = ela não manda mais preço e
    saiu da conferência. `erro`: a leitura falhou e nada foi gravado (a
    célula fica com a leitura anterior)."""

    celula: ConferenciaCelulaOut | None = None
    erro: str | None = None
    # A conferência achou anúncio vinculado e tirou o "sem anúncio vinculado"
    # (no_link) que um envio antigo tinha gravado: a tela tira a marca da
    # célula na grade aberta (19/11/2026).
    sem_vinculo_tirado: bool = False


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
    # Só o administrador muda a margem (conferir é de todos desde o M-7).
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
    # Desde o M-7 (18/11/2026) todos que veem a Tabela apertam "Conferir
    # anúncios" (o POST pede o mesmo `tabela_precos` ver do GET).
    pode_conferir: bool


def _e_admin(user: User) -> bool:
    return user.role == UserRole.ADMIN


async def _exigir_departamento(session: AsyncSession, department: str) -> UUID:
    """O segmento-raiz da aba; departamento que não existe → 400."""
    root_id = await _resolve_root_segment_id(session, department)
    if root_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": "invalid_department"})
    return root_id


async def _contas_escolhidas(
    session: AsyncSession, user: User, root_id: UUID, pedidas: list[UUID] | None
) -> list[str] | None:
    """M-14: as contas da conferência parcial, em texto e sem repetir (vão no
    payload do job). Todas precisam ser colunas deste departamento; senão
    400 'contas_invalidas'. None = o departamento inteiro.

    Revisão M-14: quem é de equipe confere só as colunas que vê na grade
    (`_contas_da_grade`, a cerca do GET e da célula); sem `pedidas`, todas
    elas, nunca o departamento inteiro — senão leria no marketplace e
    limparia as células de outra equipe. O administrador, e quem vê todas
    as contas da Tabela, seguem como antes."""
    if not _e_admin(user) and not (await _escopo_precos(session, user)).unrestricted:
        visiveis = {c.id for c in await _contas_da_grade(session, user, root_id)}
        ids = visiveis if pedidas is None else set(pedidas)
        if not ids or not ids <= visiveis:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": "contas_invalidas"})
        return sorted(str(i) for i in ids)
    if pedidas is None:
        return None
    ids = set(pedidas)
    achadas = set(
        (
            await session.execute(
                select(PricingAccount.id).where(
                    PricingAccount.id.in_(ids), PricingAccount.segment_id == root_id
                )
            )
        )
        .scalars()
        .all()
    )
    if achadas != ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": "contas_invalidas"})
    return sorted(str(i) for i in ids)


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
    contas = (job.payload or {}).get("contas")
    return ConferenciaJobOut(
        id=job.id,
        status=situacao,
        total=job.total,
        processed=job.processed,
        created_at=job.created_at,
        finished_at=job.finished_at,
        error=erro,
        result=job.result or {},
        parcial=len(contas) if contas is not None else None,
    )


@router.post(
    "/conferencia",
    response_model=JobCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
async def iniciar_conferencia(
    body: ConferenciaIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("tabela_precos", "view"))],
) -> JobCreatedOut:
    department = body.department
    root_id = await _exigir_departamento(session, department)
    contas = await _contas_escolhidas(session, user, root_id, body.contas)
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
        payload={"department": department, **({"contas": contas} if contas is not None else {})},
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
    # M-7: false = só o job e a margem (a consulta de 30 s da tela), sem as
    # células, que pesam MBs na mala.
    celulas: Annotated[bool, Query()] = True,
) -> ConferenciaOut:
    root_id = await _exigir_departamento(session, department)
    admin = _e_admin(user)
    job = await conferencia.ultimo_job(session, department)
    if job is not None and await conferencia.baixar_se_nao_executou(session, job):
        # Gravado: o andamento que a tela consulta (/api/jobs/{id}/status) e o
        # aviso de job falho passam a dizer o mesmo.
        await session.commit()
    concluido = await conferencia.ultimo_job(session, department, so_concluido=True)
    linhas: Sequence[PricingConferencia] = []
    if celulas:
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
        # totais do departamento inteiro) é do departamento todo: não sai da
        # cerca. Quem não é administrador vê no botão a data e o andamento.
        job_out.result = {}
    return ConferenciaOut(
        job=job_out,
        ultimo_concluido=concluido.id if concluido else None,
        margem=await _margem_out(session, department, root_id, user),
        pode_conferir=True,
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


@router.post("/conferencia/celula", response_model=ConferirCelulaOut)
async def conferir_celula(
    body: ConferirCelulaIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("tabela_precos", "view"))],
) -> ConferirCelulaOut:
    """Confere só uma célula (M-15): o clique no cartão vermelho."""
    prod = await session.get(PricingProduct, body.pricing_product_id)
    if prod is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "product_not_found"})
    aba = await _departamento_da_linha(session, prod)
    if aba is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail={"code": "produto_sem_departamento"}
        )
    department, root_id = aba
    # Só as colunas que esta pessoa vê na grade (a mesma régua do /grid).
    visiveis = {c.id for c in await _contas_da_grade(session, user, root_id)}
    if body.pricing_account_id not in visiveis:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "account_not_found"})
    if not conferencia.conferencia_liberada():
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "so_no_servidor"})
    # Solta a conexão antes de chamar o marketplace (o serviço abre as dele).
    await session.commit()
    try:
        linha = await conferencia.conferir_celula(
            department, body.pricing_account_id, body.pricing_product_id
        )
    except conferencia.CelulaEmConferenciaError as e:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail={"code": "celula_em_conferencia"}
        ) from e
    except conferencia.LeituraDaCelulaFalhouError as e:
        return ConferirCelulaOut(erro=str(e))
    logger.info(
        "conferencia_celula_pedida",
        department=department,
        pricing_account_id=str(body.pricing_account_id),
        pricing_product_id=str(body.pricing_product_id),
        user_id=str(user.id),
    )
    if linha is None:
        return ConferirCelulaOut()
    return ConferirCelulaOut(
        celula=ConferenciaCelulaOut(
            pricing_account_id=linha["pricing_account_id"],
            pricing_product_id=linha["pricing_product_id"],
            preco_tabela=_numero(linha["preco_tabela"]),
            situacao=linha["situacao"],
            motivo=linha["motivo"],
            conferido_em=linha["conferido_em"],
            anuncios=linha["anuncios"],
            job_id=linha["job_id"],
        ),
        sem_vinculo_tirado=bool(linha.get("sem_vinculo_tirado")),
    )


def _numero(valor: Decimal | None) -> float | None:
    return float(valor) if valor is not None else None


@router.put("/conferencia/margem", response_model=MargemOut)
async def gravar_margem(
    body: MargemIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_admin)],
) -> MargemOut:
    """A Geral do departamento e as duas caixas do menu "Margem ▾" (M-2,
    17/11/2026). Só o administrador (conferir é de todos desde o M-7)."""
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
