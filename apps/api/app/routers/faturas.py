"""Faturas CRUD (admin-only).

Assinaturas/planos recorrentes que o admin acompanha (ex.: plano de 12 meses
do Higgsfield). Um cron avisa 1 dia antes do vencimento via alerta na tela.
Renovação é manual: o admin edita `data_vencimento` pro próximo ciclo.
"""

from datetime import date, timedelta
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import asc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_admin
from app.models import Fatura, User
from app.schemas.faturas import FaturaCreate, FaturaOut, FaturaPatch

logger = structlog.get_logger()
router = APIRouter(prefix="/api/faturas", tags=["faturas"])


def _to_out(f: Fatura) -> FaturaOut:
    return FaturaOut(
        id=f.id,
        servico=f.servico,
        plano=f.plano,
        valor=f.valor,
        data_vencimento=f.data_vencimento,
        created_by=f.created_by,
        created_at=f.created_at,
        updated_at=f.updated_at,
    )


@router.get("/vencendo-count")
async def vencendo_count(
    session: Annotated[AsyncSession, Depends(get_session)],
    _admin: Annotated[User, Depends(require_admin)],
) -> dict[str, int]:
    """Conta faturas vencidas ou vencendo (`data_vencimento <= hoje+1`) —
    alimenta a bolinha vermelha no item Faturas da sidebar. Continua contando
    as vencidas até o admin renovar (mover a data) ou apagar."""
    limit = date.today() + timedelta(days=1)
    n = (
        await session.execute(
            select(func.count())
            .select_from(Fatura)
            .where(Fatura.data_vencimento <= limit)
        )
    ).scalar_one()
    return {"count": int(n or 0)}


@router.get("", response_model=list[FaturaOut])
async def list_faturas(
    session: Annotated[AsyncSession, Depends(get_session)],
    _admin: Annotated[User, Depends(require_admin)],
) -> list[FaturaOut]:
    # Mais perto do vencimento primeiro.
    stmt = select(Fatura).order_by(asc(Fatura.data_vencimento), asc(Fatura.servico))
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_out(f) for f in rows]


@router.post("", response_model=FaturaOut, status_code=status.HTTP_201_CREATED)
async def create_fatura(
    body: FaturaCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin: Annotated[User, Depends(require_admin)],
) -> FaturaOut:
    f = Fatura(
        servico=body.servico.strip(),
        plano=body.plano.strip() if body.plano else None,
        valor=body.valor,
        data_vencimento=body.data_vencimento,
        created_by=admin.id,
    )
    session.add(f)
    await session.commit()
    await session.refresh(f)
    logger.info("fatura_created", id=str(f.id), servico=f.servico)
    return _to_out(f)


@router.patch("/{fatura_id}", response_model=FaturaOut)
async def patch_fatura(
    fatura_id: UUID,
    body: FaturaPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _admin: Annotated[User, Depends(require_admin)],
) -> FaturaOut:
    f = (
        await session.execute(select(Fatura).where(Fatura.id == fatura_id))
    ).scalar_one_or_none()
    if f is None:
        raise HTTPException(404, detail={"code": "fatura_not_found"})

    data = body.model_dump(exclude_unset=True)
    if "servico" in data and data["servico"] is not None:
        f.servico = data["servico"].strip()
    if "plano" in data:
        f.plano = data["plano"].strip() if data["plano"] else None
    if "valor" in data:
        f.valor = data["valor"]
    if "data_vencimento" in data and data["data_vencimento"] is not None:
        f.data_vencimento = data["data_vencimento"]

    await session.commit()
    await session.refresh(f)
    return _to_out(f)


@router.delete("/{fatura_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_fatura(
    fatura_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _admin: Annotated[User, Depends(require_admin)],
) -> None:
    f = (
        await session.execute(select(Fatura).where(Fatura.id == fatura_id))
    ).scalar_one_or_none()
    if f is None:
        raise HTTPException(404, detail={"code": "fatura_not_found"})
    await session.delete(f)
    await session.commit()
    logger.info("fatura_deleted", id=str(fatura_id))
    return None
