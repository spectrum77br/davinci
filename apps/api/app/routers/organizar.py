"""Produtos › Organizar (17/11/2026): esconde da TELA o que é inválido.

Só ADM nos 4 endpoints (require_admin → 403 {"code": "admin_only"}); na tela o
botão nem aparece para os outros. O que foi ocultado some para TODOS, só nas
telas Produtos e Anúncios. Nada é apagado e nenhum robô lê isto — ver
services/organizar.py. Os dois POST rodam na própria requisição (um INSERT de
milhares de linhas leva menos de 1 s), sem job.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_admin
from app.models import User
from app.services import organizar as svc

logger = structlog.get_logger()
router = APIRouter(prefix="/api/produtos/organizar", tags=["products"])


class AplicarIn(BaseModel):
    token: str
    chaves: list[str]


class ReexibirIn(BaseModel):
    lote_id: UUID | None = None
    marcador_ids: list[UUID] | None = None
    chave: str | None = None


def _http(e: svc.OrganizarError) -> HTTPException:
    return HTTPException(e.status, detail={"code": e.code})


@router.get("/previa")
async def previa(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_admin)],
) -> dict:
    """Quantidade por chave (o que ainda aparece e é inválido), os ocultos de
    agora e os lotes. Os ids mostrados ficam no servidor por 30 min (token)."""
    out = await svc.previa(session, user)
    await session.commit()
    return out


@router.get("/itens")
async def itens(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_admin)],
    chave: str = Query(...),
    ocultos: bool = Query(False, description="true = os itens já ocultos desta chave"),
    pagina: int = Query(1, ge=1),
    token: str | None = Query(
        None,
        description="o token da prévia: com ocultos=false, lista só o que ela mostrou "
        "e ainda vale (o mesmo que o Ocultar grava); vencido → 409 previa_expirada",
    ),
) -> dict:
    try:
        return await svc.itens(session, chave, ocultos, pagina, token=token, user=user)
    except svc.OrganizarError as e:
        raise _http(e) from None


@router.post("/aplicar")
async def aplicar(
    body: AplicarIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_admin)],
) -> dict:
    """Oculta o que a prévia mostrou (e ainda vale) nas chaves escolhidas."""
    try:
        out = await svc.aplicar(session, user, body.token, body.chaves)
    except svc.OrganizarError as e:
        await session.rollback()
        raise _http(e) from None
    await session.commit()
    await svc.atualizar_estatisticas(session)
    logger.info(
        "organizar_aplicado",
        actor_user_id=str(user.id),
        lote_id=out["lote_id"],
        ocultados=out["ocultados"],
        por_chave=out["por_chave"],
        sairam_desde_previa=out["sairam_desde_previa"],
    )
    return out


@router.post("/reexibir")
async def reexibir(
    body: ReexibirIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_admin)],
) -> dict:
    """"Mostrar de novo" por lote, por item (marcador_ids) ou por chave."""
    try:
        out = await svc.reexibir(
            session,
            user,
            lote_id=body.lote_id,
            marcador_ids=body.marcador_ids,
            chave=body.chave,
        )
    except svc.OrganizarError as e:
        await session.rollback()
        raise _http(e) from None
    await session.commit()
    await svc.atualizar_estatisticas(session)
    logger.info(
        "organizar_reexibido",
        actor_user_id=str(user.id),
        lote_id=str(body.lote_id) if body.lote_id else None,
        itens=len(body.marcador_ids or []),
        chave=body.chave,
        reexibidos=out["reexibidos"],
    )
    return out
