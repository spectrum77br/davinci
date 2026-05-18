"""Tarefas CRUD.

Admin: full CRUD over every row. Regular user: read-only view of rows
where they are the responsavel, except `observacao` which they can edit
on their own rows. The spec used PUT for update; we use PATCH to match
the rest of the codebase (partial updates).
"""

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import case, desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db import get_session
from app.deps.auth import require_active_user, require_admin
from app.models import Tarefa, User, UserRole
from app.schemas.tarefas import TarefaCreate, TarefaOut, TarefaPatch

logger = structlog.get_logger()
router = APIRouter(prefix="/api/tarefas", tags=["tarefas"])


def _is_admin(u: User) -> bool:
    return u.role == UserRole.ADMIN


def _to_out(t: Tarefa, responsavel: User | None) -> TarefaOut:
    return TarefaOut(
        id=t.id,
        responsavel_id=t.responsavel_id,
        responsavel_name=responsavel.name if responsavel else None,
        responsavel_email=responsavel.email if responsavel else None,
        data_inicio=t.data_inicio,
        data_conclusao=t.data_conclusao,
        tarefa=t.tarefa,
        observacao=t.observacao,
        created_by=t.created_by,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


@router.get("", response_model=list[TarefaOut])
async def list_tarefas(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
) -> list[TarefaOut]:
    resp = aliased(User)
    stmt = (
        select(Tarefa, resp)
        .outerjoin(resp, resp.id == Tarefa.responsavel_id)
        # Pending (data_conclusao IS NULL) first, then by start date DESC.
        .order_by(
            case((Tarefa.data_conclusao.is_(None), 0), else_=1),
            desc(Tarefa.data_inicio),
            desc(Tarefa.created_at),
        )
    )
    if not _is_admin(user):
        stmt = stmt.where(Tarefa.responsavel_id == user.id)
    rows = (await session.execute(stmt)).all()
    return [_to_out(t, r) for (t, r) in rows]


@router.post("", response_model=TarefaOut, status_code=status.HTTP_201_CREATED)
async def create_tarefa(
    body: TarefaCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin: Annotated[User, Depends(require_admin)],
) -> TarefaOut:
    # Validate responsavel exists.
    resp = (
        await session.execute(select(User).where(User.id == body.responsavel_id))
    ).scalar_one_or_none()
    if resp is None:
        raise HTTPException(404, detail={"code": "responsavel_not_found"})

    t = Tarefa(
        responsavel_id=body.responsavel_id,
        data_inicio=body.data_inicio,
        tarefa=body.tarefa.strip(),
        created_by=admin.id,
    )
    session.add(t)
    await session.commit()
    await session.refresh(t)
    logger.info("tarefa_created", id=str(t.id), responsavel_id=str(t.responsavel_id))
    return _to_out(t, resp)


@router.patch("/{tarefa_id}", response_model=TarefaOut)
async def patch_tarefa(
    tarefa_id: UUID,
    body: TarefaPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
) -> TarefaOut:
    t = (
        await session.execute(select(Tarefa).where(Tarefa.id == tarefa_id))
    ).scalar_one_or_none()
    if t is None:
        raise HTTPException(404, detail={"code": "tarefa_not_found"})

    is_admin = _is_admin(user)
    if not is_admin and t.responsavel_id != user.id:
        # Don't leak existence — same 404 as missing.
        raise HTTPException(404, detail={"code": "tarefa_not_found"})

    data = body.model_dump(exclude_unset=True)

    if is_admin:
        if "responsavel_id" in data and data["responsavel_id"] is not None:
            new_resp = (
                await session.execute(select(User).where(User.id == data["responsavel_id"]))
            ).scalar_one_or_none()
            if new_resp is None:
                raise HTTPException(404, detail={"code": "responsavel_not_found"})
            t.responsavel_id = data["responsavel_id"]
        if "data_inicio" in data and data["data_inicio"] is not None:
            t.data_inicio = data["data_inicio"]
        if "data_conclusao" in data:
            # Explicit None unsets it (mark as pending again).
            t.data_conclusao = data["data_conclusao"]
        if "tarefa" in data and data["tarefa"] is not None:
            t.tarefa = data["tarefa"].strip()
        if "observacao" in data:
            t.observacao = data["observacao"]
    else:
        # Regular user: ignore everything except observacao.
        if "observacao" in data:
            t.observacao = data["observacao"]
        forbidden = set(data) - {"observacao"}
        if forbidden:
            logger.info(
                "tarefa_patch_dropped_admin_fields",
                user_id=str(user.id),
                tarefa_id=str(t.id),
                ignored=sorted(forbidden),
            )

    await session.commit()
    await session.refresh(t)
    resp = (
        await session.execute(select(User).where(User.id == t.responsavel_id))
    ).scalar_one_or_none()
    return _to_out(t, resp)


@router.delete("/{tarefa_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tarefa(
    tarefa_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _admin: Annotated[User, Depends(require_admin)],
) -> None:
    t = (
        await session.execute(select(Tarefa).where(Tarefa.id == tarefa_id))
    ).scalar_one_or_none()
    if t is None:
        raise HTTPException(404, detail={"code": "tarefa_not_found"})
    await session.delete(t)
    await session.commit()
    logger.info("tarefa_deleted", id=str(tarefa_id))
    return None
