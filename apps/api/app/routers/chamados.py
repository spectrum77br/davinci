"""Chamados de pós-venda — casos a acompanhar (CRUD manual).

Formato da Planilha2 de atualização1.xlsx. Cada chamado guarda os dados do
pedido + a assinatura de status do Meli (`meli_status`). A partir dessa
assinatura, `/sugestao` devolve os Status Bling candidatos que a curadoria da
planilha já viu — é só uma dica; a classificação final é do operador. Gated
pelo recurso `chamados`.
"""

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import Chamado, User
from app.schemas.chamados import (
    CandidatoOut,
    ChamadoCreate,
    ChamadoOut,
    ChamadoPatch,
    OpcoesOut,
    SugestaoIn,
    SugestaoOut,
)
from app.services import chamados_rules

logger = structlog.get_logger()
router = APIRouter(prefix="/api/chamados", tags=["chamados"])


def _to_out(c: Chamado) -> ChamadoOut:
    return ChamadoOut(
        id=c.id,
        data=c.data,
        pedido_bling=c.pedido_bling,
        pedido_marketplace=c.pedido_marketplace,
        plataforma=c.plataforma,
        conta=c.conta,
        meli_status=c.meli_status or {},
        localizacao=c.localizacao,
        status_bling=c.status_bling,
        observacao=c.observacao,
        created_by=c.created_by,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


def _clean(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    return v or None


def _clean_meli(m: dict[str, str] | None) -> dict[str, str]:
    """Mantém só os campos conhecidos com valor não-vazio."""
    if not m:
        return {}
    return {
        f: str(m[f]).strip()
        for f in chamados_rules.FIELD_ORDER
        if m.get(f) and str(m[f]).strip()
    }


@router.get("/opcoes", response_model=OpcoesOut)
async def opcoes(
    _user: Annotated[User, Depends(require_permission("chamados", "view"))],
) -> OpcoesOut:
    return OpcoesOut(
        field_order=chamados_rules.FIELD_ORDER,
        field_labels=chamados_rules.FIELD_LABELS,
        field_options=chamados_rules.FIELD_OPTIONS,
    )


@router.post("/sugestao", response_model=SugestaoOut)
async def sugestao(
    body: SugestaoIn,
    _user: Annotated[User, Depends(require_permission("chamados", "view"))],
) -> SugestaoOut:
    cand = chamados_rules.sugerir(body.meli_status)
    return SugestaoOut(candidatos=[CandidatoOut(**c) for c in cand])


@router.get("", response_model=list[ChamadoOut])
async def list_chamados(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("chamados", "view"))],
) -> list[ChamadoOut]:
    # Mais recentes primeiro (data desc, depois criação desc).
    stmt = select(Chamado).order_by(
        desc(Chamado.data.is_(None)), desc(Chamado.data), desc(Chamado.created_at)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_out(c) for c in rows]


@router.post("", response_model=ChamadoOut, status_code=status.HTTP_201_CREATED)
async def create_chamado(
    body: ChamadoCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("chamados", "edit"))],
) -> ChamadoOut:
    c = Chamado(
        data=body.data,
        pedido_bling=_clean(body.pedido_bling),
        pedido_marketplace=_clean(body.pedido_marketplace),
        plataforma=_clean(body.plataforma),
        conta=_clean(body.conta),
        meli_status=_clean_meli(body.meli_status),
        localizacao=_clean(body.localizacao),
        status_bling=_clean(body.status_bling),
        observacao=_clean(body.observacao),
        created_by=user.id,
    )
    session.add(c)
    await session.commit()
    await session.refresh(c)
    logger.info("chamado_created", id=str(c.id), pedido_bling=c.pedido_bling)
    return _to_out(c)


@router.patch("/{chamado_id}", response_model=ChamadoOut)
async def patch_chamado(
    chamado_id: UUID,
    body: ChamadoPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("chamados", "edit"))],
) -> ChamadoOut:
    c = (
        await session.execute(select(Chamado).where(Chamado.id == chamado_id))
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "chamado_not_found"})

    data = body.model_dump(exclude_unset=True)
    if "data" in data:
        c.data = data["data"]
    if "pedido_bling" in data:
        c.pedido_bling = _clean(data["pedido_bling"])
    if "pedido_marketplace" in data:
        c.pedido_marketplace = _clean(data["pedido_marketplace"])
    if "plataforma" in data:
        c.plataforma = _clean(data["plataforma"])
    if "conta" in data:
        c.conta = _clean(data["conta"])
    if "meli_status" in data:
        c.meli_status = _clean_meli(data["meli_status"])
    if "localizacao" in data:
        c.localizacao = _clean(data["localizacao"])
    if "status_bling" in data:
        c.status_bling = _clean(data["status_bling"])
    if "observacao" in data:
        c.observacao = _clean(data["observacao"])

    await session.commit()
    await session.refresh(c)
    return _to_out(c)


@router.delete("/{chamado_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chamado(
    chamado_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("chamados", "edit"))],
) -> None:
    c = (
        await session.execute(select(Chamado).where(Chamado.id == chamado_id))
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "chamado_not_found"})
    await session.delete(c)
    await session.commit()
    logger.info("chamado_deleted", id=str(chamado_id))
    return None
