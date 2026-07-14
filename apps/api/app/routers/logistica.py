"""Logística — casos de pós-venda a acompanhar (CRUD manual) + aba Status.

Aba "Logística": formato da planilha (Data | pedido bling | pedido marketplace
| plataforma | conta | STATUS PLATAFORMA | rastreio | localização | STATUS
BLING | Chamado). Cada linha guarda os dados do pedido + a assinatura de status
do Meli (`meli_status`); a partir dela, `/sugestao` devolve os Status Bling
candidatos que a curadoria da planilha já viu — é só uma dica, a classificação
final é do operador.

Aba "Status": cadastro/referência do que fazer pra cada STATUS PLATAFORMA
(alterar status bling, abrir chamado, mensagem). Por enquanto é só cadastro —
não escreve no Bling automaticamente.

Gated pelo recurso `logistica`.
"""

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import Logistica, LogisticaStatus, User
from app.schemas.logistica import (
    CandidatoOut,
    LogisticaCreate,
    LogisticaOut,
    LogisticaPatch,
    LogisticaStatusCreate,
    LogisticaStatusOut,
    LogisticaStatusPatch,
    OpcoesOut,
    SugestaoIn,
    SugestaoOut,
)
from app.services import logistica_rules

logger = structlog.get_logger()
router = APIRouter(prefix="/api/logistica", tags=["logistica"])


def _to_out(c: Logistica) -> LogisticaOut:
    return LogisticaOut(
        id=c.id,
        data=c.data,
        pedido_bling=c.pedido_bling,
        pedido_marketplace=c.pedido_marketplace,
        plataforma=c.plataforma,
        conta=c.conta,
        meli_status=c.meli_status or {},
        rastreio=c.rastreio,
        localizacao=c.localizacao,
        status_bling=c.status_bling,
        chamado=c.chamado,
        observacao=c.observacao,
        created_by=c.created_by,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


def _to_status_out(s: LogisticaStatus) -> LogisticaStatusOut:
    return LogisticaStatusOut(
        id=s.id,
        status_plataforma=s.status_plataforma,
        alterar_status_bling=s.alterar_status_bling,
        abrir_chamado=s.abrir_chamado,
        mensagem_chamado=s.mensagem_chamado,
        created_by=s.created_by,
        created_at=s.created_at,
        updated_at=s.updated_at,
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
        for f in logistica_rules.FIELD_ORDER
        if m.get(f) and str(m[f]).strip()
    }


# ---- Opções + sugestão ----


@router.get("/opcoes", response_model=OpcoesOut)
async def opcoes(
    _user: Annotated[User, Depends(require_permission("logistica", "view"))],
) -> OpcoesOut:
    return OpcoesOut(
        field_order=logistica_rules.FIELD_ORDER,
        field_labels=logistica_rules.FIELD_LABELS,
        field_options=logistica_rules.FIELD_OPTIONS,
    )


@router.post("/sugestao", response_model=SugestaoOut)
async def sugestao(
    body: SugestaoIn,
    _user: Annotated[User, Depends(require_permission("logistica", "view"))],
) -> SugestaoOut:
    cand = logistica_rules.sugerir(body.meli_status)
    return SugestaoOut(candidatos=[CandidatoOut(**c) for c in cand])


# ---- Aba Status (cadastro) ----


@router.get("/status", response_model=list[LogisticaStatusOut])
async def list_status(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "view"))],
) -> list[LogisticaStatusOut]:
    stmt = select(LogisticaStatus).order_by(LogisticaStatus.status_plataforma)
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_status_out(s) for s in rows]


@router.post("/status", response_model=LogisticaStatusOut, status_code=status.HTTP_201_CREATED)
async def create_status(
    body: LogisticaStatusCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> LogisticaStatusOut:
    s = LogisticaStatus(
        status_plataforma=(body.status_plataforma or "").strip(),
        alterar_status_bling=_clean(body.alterar_status_bling),
        abrir_chamado=bool(body.abrir_chamado),
        mensagem_chamado=_clean(body.mensagem_chamado),
        created_by=user.id,
    )
    session.add(s)
    await session.commit()
    await session.refresh(s)
    return _to_status_out(s)


@router.patch("/status/{status_id}", response_model=LogisticaStatusOut)
async def patch_status(
    status_id: UUID,
    body: LogisticaStatusPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> LogisticaStatusOut:
    s = (
        await session.execute(select(LogisticaStatus).where(LogisticaStatus.id == status_id))
    ).scalar_one_or_none()
    if s is None:
        raise HTTPException(404, detail={"code": "logistica_status_not_found"})

    data = body.model_dump(exclude_unset=True)
    if "status_plataforma" in data:
        s.status_plataforma = (data["status_plataforma"] or "").strip()
    if "alterar_status_bling" in data:
        s.alterar_status_bling = _clean(data["alterar_status_bling"])
    if "abrir_chamado" in data:
        s.abrir_chamado = bool(data["abrir_chamado"])
    if "mensagem_chamado" in data:
        s.mensagem_chamado = _clean(data["mensagem_chamado"])

    await session.commit()
    await session.refresh(s)
    return _to_status_out(s)


@router.delete("/status/{status_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_status(
    status_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> None:
    s = (
        await session.execute(select(LogisticaStatus).where(LogisticaStatus.id == status_id))
    ).scalar_one_or_none()
    if s is None:
        raise HTTPException(404, detail={"code": "logistica_status_not_found"})
    await session.delete(s)
    await session.commit()
    return None


# ---- Aba Logística (casos) ----


@router.get("", response_model=list[LogisticaOut])
async def list_logistica(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "view"))],
) -> list[LogisticaOut]:
    # Mais recentes primeiro (data desc, depois criação desc).
    stmt = select(Logistica).order_by(
        desc(Logistica.data.is_(None)), desc(Logistica.data), desc(Logistica.created_at)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_out(c) for c in rows]


@router.post("", response_model=LogisticaOut, status_code=status.HTTP_201_CREATED)
async def create_logistica(
    body: LogisticaCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> LogisticaOut:
    c = Logistica(
        data=body.data,
        pedido_bling=_clean(body.pedido_bling),
        pedido_marketplace=_clean(body.pedido_marketplace),
        plataforma=_clean(body.plataforma),
        conta=_clean(body.conta),
        meli_status=_clean_meli(body.meli_status),
        rastreio=_clean(body.rastreio),
        localizacao=_clean(body.localizacao),
        status_bling=_clean(body.status_bling),
        chamado=_clean(body.chamado),
        observacao=_clean(body.observacao),
        created_by=user.id,
    )
    session.add(c)
    await session.commit()
    await session.refresh(c)
    logger.info("logistica_created", id=str(c.id), pedido_bling=c.pedido_bling)
    return _to_out(c)


@router.patch("/{logistica_id}", response_model=LogisticaOut)
async def patch_logistica(
    logistica_id: UUID,
    body: LogisticaPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> LogisticaOut:
    c = (
        await session.execute(select(Logistica).where(Logistica.id == logistica_id))
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "logistica_not_found"})

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
    if "rastreio" in data:
        c.rastreio = _clean(data["rastreio"])
    if "localizacao" in data:
        c.localizacao = _clean(data["localizacao"])
    if "status_bling" in data:
        c.status_bling = _clean(data["status_bling"])
    if "chamado" in data:
        c.chamado = _clean(data["chamado"])
    if "observacao" in data:
        c.observacao = _clean(data["observacao"])

    await session.commit()
    await session.refresh(c)
    return _to_out(c)


@router.delete("/{logistica_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_logistica(
    logistica_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> None:
    c = (
        await session.execute(select(Logistica).where(Logistica.id == logistica_id))
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "logistica_not_found"})
    await session.delete(c)
    await session.commit()
    logger.info("logistica_deleted", id=str(logistica_id))
    return None
