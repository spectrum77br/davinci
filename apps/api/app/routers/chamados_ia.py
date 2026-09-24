"""Aba Chamados › IA de Chamado (Vinicius, 24/09/2026).

"Essa aba que vai criar é onde eu vou ensinar ele, e onde vai criando um manual —
quando acontecer isso e isso você faz isso". A IA de Chamado é o cérebro dos
chamados (hoje o Hermes no Mac Santiago, `chamados_cerebros`); aqui a pessoa:

- liga/desliga (desligada, ela não recebe caso nenhum e não decide nada — é o
  próprio servidor que trava, ver `routers/chamados.agent_analisar/analise`);
- escreve o manual (`chamados_ia_regras`), que a IA lê a cada passada;
- vê o que ela decidiu (as análises que ela gravou nos chamados).

Sem modo teste (decisão dele): ligada = decide de verdade.
"""

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import Chamado, ChamadoCerebro, ChamadoIaRegra, ChamadoMensagem, User
from app.models.chamado import CANAIS
from app.routers.chamados import _trabalho_do_cerebro
from app.schemas.chamados import (
    IaDecisaoOut,
    IaEstadoIn,
    IaEstadoOut,
    IaRegraIn,
    IaRegraOut,
    IaRegraPatch,
)

logger = structlog.get_logger()
# incluído ANTES do router da aba (senão "/ia" cairia em "/{chamado_id}")
router = APIRouter(prefix="/api/chamados/ia", tags=["chamados"])

_DECISOES = 40


def _nome(user: User | None) -> str | None:
    if user is None:
        return None
    return (user.name or user.email or "").strip() or None


def _plataforma(valor: str | None) -> str | None:
    v = (valor or "").strip().lower()
    return None if v in ("", "todas") else v


async def _ia(session: AsyncSession) -> ChamadoCerebro:
    ia = (
        await session.execute(
            select(ChamadoCerebro)
            .where(ChamadoCerebro.revoked_at.is_(None))
            .order_by(ChamadoCerebro.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    if ia is None:
        raise HTTPException(404, detail={"code": "ia_sem_cadastro"})
    return ia


async def _regras_out(session: AsyncSession) -> list[IaRegraOut]:
    regras = (
        (await session.execute(select(ChamadoIaRegra).order_by(ChamadoIaRegra.created_at)))
        .scalars()
        .all()
    )
    ids = {r.updated_by or r.created_by for r in regras} - {None}
    nomes: dict[UUID, str | None] = {}
    if ids:
        for u in (await session.execute(select(User).where(User.id.in_(ids)))).scalars():
            nomes[u.id] = _nome(u)
    return [
        IaRegraOut(
            id=r.id,
            quando=r.quando,
            faca=r.faca,
            plataforma=r.plataforma,
            ativa=r.ativa,
            autor=nomes.get(r.updated_by or r.created_by),
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in regras
    ]


@router.get("", response_model=IaEstadoOut)
async def estado(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("chamados", "view"))],
) -> IaEstadoOut:
    ia = await _ia(session)
    esperando = (
        await session.execute(
            select(func.count()).select_from(
                _trabalho_do_cerebro(None, list(CANAIS)).order_by(None).subquery()
            )
        )
    ).scalar_one()
    decisoes = (
        await session.execute(
            select(ChamadoMensagem, Chamado)
            .join(Chamado, Chamado.id == ChamadoMensagem.chamado_id)
            .where(ChamadoMensagem.tipo == "analise", ChamadoMensagem.autor_nome == ia.nome)
            .order_by(ChamadoMensagem.created_at.desc())
            .limit(_DECISOES)
        )
    ).all()
    return IaEstadoOut(
        nome=ia.nome,
        ligada=ia.ligada,
        exclusivo=ia.exclusivo,
        ultima_passada=ia.last_used_at,
        esperando=esperando,
        regras=await _regras_out(session),
        decisoes=[
            IaDecisaoOut(
                chamado_id=ch.id,
                pedido_bling=ch.pedido_bling,
                plataforma=ch.plataforma,
                conta=ch.conta,
                quando=m.created_at,
                texto=m.texto,
            )
            for m, ch in decisoes
        ],
    )


@router.patch("", response_model=IaEstadoOut)
async def ligar(
    body: IaEstadoIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("chamados", "edit"))],
) -> IaEstadoOut:
    ia = await _ia(session)
    if ia.ligada != body.ligada:
        ia.ligada = body.ligada
        await session.commit()
        logger.info("chamados_ia_ligada", ligada=ia.ligada, por=_nome(user))
    return await estado(session, user)


@router.post("/regras", response_model=IaRegraOut, status_code=status.HTTP_201_CREATED)
async def criar_regra(
    body: IaRegraIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("chamados", "edit"))],
) -> IaRegraOut:
    r = ChamadoIaRegra(
        quando=body.quando,
        faca=body.faca,
        plataforma=_plataforma(body.plataforma),
        created_by=user.id,
        updated_by=user.id,
    )
    session.add(r)
    await session.commit()
    await session.refresh(r)
    logger.info("chamados_ia_regra_criada", regra=str(r.id), por=_nome(user))
    return next(x for x in await _regras_out(session) if x.id == r.id)


@router.patch("/regras/{regra_id}", response_model=IaRegraOut)
async def editar_regra(
    regra_id: UUID,
    body: IaRegraPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("chamados", "edit"))],
) -> IaRegraOut:
    r = await session.get(ChamadoIaRegra, regra_id)
    if r is None:
        raise HTTPException(404, detail={"code": "regra_nao_encontrada"})
    campos = body.model_fields_set
    if "quando" in campos:
        if not body.quando:
            raise HTTPException(422, detail={"code": "quando_vazio"})
        r.quando = body.quando
    if "faca" in campos:
        if not body.faca:
            raise HTTPException(422, detail={"code": "faca_vazio"})
        r.faca = body.faca
    if "plataforma" in campos:
        r.plataforma = _plataforma(body.plataforma)
    if "ativa" in campos and body.ativa is not None:
        r.ativa = body.ativa
    r.updated_by = user.id
    await session.commit()
    await session.refresh(r)
    return next(x for x in await _regras_out(session) if x.id == r.id)


@router.delete("/regras/{regra_id}", status_code=status.HTTP_204_NO_CONTENT)
async def apagar_regra(
    regra_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("chamados", "edit"))],
) -> Response:
    r = await session.get(ChamadoIaRegra, regra_id)
    if r is None:
        raise HTTPException(404, detail={"code": "regra_nao_encontrada"})
    await session.delete(r)
    await session.commit()
    logger.info("chamados_ia_regra_apagada", regra=str(regra_id), por=_nome(user))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
