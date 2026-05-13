"""Margens — per-order margin rows with approval status."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import BlingOrder, Margens, User
from app.schemas.margens import ALLOWED_STATUS, MargensOut, MargensPatch
from app.services.bling_orders import _bling_client_for_user

logger = structlog.get_logger()
router = APIRouter(prefix="/api/margens", tags=["margens"])

SITUACAO_APROVADO = 6
SITUACAO_REPROVADO = 83955


@router.get("", response_model=list[MargensOut])
async def list_margens(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("margem", "view"))],
    status: str | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
) -> list[MargensOut]:
    stmt = select(Margens)
    if status:
        if status not in ALLOWED_STATUS:
            raise HTTPException(400, detail={"code": "invalid_status"})
        stmt = stmt.where(Margens.status == status)
    stmt = stmt.order_by(Margens.data.desc().nullslast(), Margens.created_at.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [MargensOut.model_validate(r) for r in rows]


@router.patch("/{margem_id}", response_model=MargensOut)
async def patch_margens(
    margem_id: UUID,
    body: MargensPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("margem", "edit"))],
) -> MargensOut:
    row = (
        await session.execute(select(Margens).where(Margens.id == margem_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "margem_not_found"})

    data = body.model_dump(exclude_unset=True)
    new_status = data.get("status")
    if "status" in data and new_status is not None and new_status not in ALLOWED_STATUS:
        raise HTTPException(400, detail={"code": "invalid_status"})

    if new_status in ("Aprovado", "Reprovado"):
        await _apply_bling_decision(session, user.id, row.pedido_bling, new_status)

    for k, v in data.items():
        setattr(row, k, v)
    await session.commit()
    await session.refresh(row)
    logger.info(
        "margens_patched",
        margem_id=str(row.id),
        pedido_bling=row.pedido_bling,
        status=row.status,
    )
    return MargensOut.model_validate(row)


async def _apply_bling_decision(
    session: AsyncSession,
    user_id: UUID,
    pedido_bling: int | None,
    new_status: str,
) -> None:
    if pedido_bling is None:
        raise HTTPException(400, detail={"code": "pedido_bling_missing"})

    bling_id = (
        await session.execute(
            select(BlingOrder.bling_id)
            .where(BlingOrder.numero == str(pedido_bling))
            .where(BlingOrder.bling_id.is_not(None))
            .limit(1)
        )
    ).scalar_one_or_none()
    if bling_id is None:
        raise HTTPException(404, detail={"code": "bling_order_not_found"})

    client = await _bling_client_for_user(session, user_id)
    if client is None:
        raise HTTPException(400, detail={"code": "bling_integration_missing"})

    situacao_id = SITUACAO_APROVADO if new_status == "Aprovado" else SITUACAO_REPROVADO
    try:
        await client.update_order_situacao(int(bling_id), situacao_id)
    except httpx.HTTPStatusError as e:
        code = e.response.status_code if e.response is not None else 0
        body = e.response.text[:300] if e.response is not None else ""
        logger.warning(
            "bling_situacao_patch_failed",
            bling_id=bling_id,
            situacao_id=situacao_id,
            http=code,
            body=body,
        )
        raise HTTPException(
            502,
            detail={"code": "bling_patch_failed", "http": code},
        ) from e

    await session.execute(
        update(BlingOrder)
        .where(BlingOrder.numero == str(pedido_bling))
        .where(BlingOrder.verificado.is_not(True))
        .values(verificado=True)
    )
