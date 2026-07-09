"""Automações — catálogo manual das rotinas/crons do sistema.

Registro editável que o admin mantém pra ter visibilidade do que está rodando
(sync de pedidos, refresh de tokens, discrepâncias, faturas, etc). NÃO executa
nem controla nada — é só documentação viva. Gated pelo recurso `integracoes`
(mesma aba da tela de Integrações).
"""

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import asc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import Automacao, User
from app.schemas.automacoes import AutomacaoCreate, AutomacaoOut, AutomacaoPatch

logger = structlog.get_logger()
router = APIRouter(prefix="/api/automacoes", tags=["automacoes"])


def _to_out(a: Automacao) -> AutomacaoOut:
    return AutomacaoOut(
        id=a.id,
        nome=a.nome,
        descricao=a.descricao,
        frequencia=a.frequencia,
        categoria=a.categoria,
        ativa=a.ativa,
        created_by=a.created_by,
        created_at=a.created_at,
        updated_at=a.updated_at,
    )


def _clean(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    return v or None


@router.get("", response_model=list[AutomacaoOut])
async def list_automacoes(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("integracoes", "view"))],
) -> list[AutomacaoOut]:
    # Agrupadas por categoria, depois nome (alfabético).
    stmt = select(Automacao).order_by(
        asc(Automacao.categoria), asc(Automacao.nome)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_out(a) for a in rows]


@router.post("", response_model=AutomacaoOut, status_code=status.HTTP_201_CREATED)
async def create_automacao(
    body: AutomacaoCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("integracoes", "edit"))],
) -> AutomacaoOut:
    a = Automacao(
        nome=body.nome.strip(),
        descricao=_clean(body.descricao),
        frequencia=_clean(body.frequencia),
        categoria=_clean(body.categoria),
        ativa=body.ativa,
        created_by=user.id,
    )
    session.add(a)
    await session.commit()
    await session.refresh(a)
    logger.info("automacao_created", id=str(a.id), nome=a.nome)
    return _to_out(a)


@router.patch("/{automacao_id}", response_model=AutomacaoOut)
async def patch_automacao(
    automacao_id: UUID,
    body: AutomacaoPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("integracoes", "edit"))],
) -> AutomacaoOut:
    a = (
        await session.execute(select(Automacao).where(Automacao.id == automacao_id))
    ).scalar_one_or_none()
    if a is None:
        raise HTTPException(404, detail={"code": "automacao_not_found"})

    data = body.model_dump(exclude_unset=True)
    if "nome" in data and data["nome"] is not None:
        a.nome = data["nome"].strip()
    if "descricao" in data:
        a.descricao = _clean(data["descricao"])
    if "frequencia" in data:
        a.frequencia = _clean(data["frequencia"])
    if "categoria" in data:
        a.categoria = _clean(data["categoria"])
    if "ativa" in data and data["ativa"] is not None:
        a.ativa = data["ativa"]

    await session.commit()
    await session.refresh(a)
    return _to_out(a)


@router.delete("/{automacao_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_automacao(
    automacao_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("integracoes", "edit"))],
) -> None:
    a = (
        await session.execute(select(Automacao).where(Automacao.id == automacao_id))
    ).scalar_one_or_none()
    if a is None:
        raise HTTPException(404, detail={"code": "automacao_not_found"})
    await session.delete(a)
    await session.commit()
    logger.info("automacao_deleted", id=str(automacao_id))
    return None
