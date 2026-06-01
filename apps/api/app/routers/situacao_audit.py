"""Consulta da trilha de auditoria de mudanças de situação no Bling.

Lista quem (ou sistema) mudou a situação de quais pedidos no Bling, quando e
de qual situação para qual. Só registra mudanças feitas PELO app — ver
`app/services/situacao_audit.py`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import BlingSituacaoAudit, User
from app.schemas.situacao_audit import SituacaoAuditOut, SituacaoAuditPage

router = APIRouter(prefix="/api", tags=["situacao-audit"])


@router.get("/situacao-audit", response_model=SituacaoAuditPage)
async def list_situacao_audit(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("sincronizacoes", "view"))],
    pedido_bling: str | None = Query(None),
    origem: str | None = Query(None),
    mudado_por: UUID | None = Query(None),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> SituacaoAuditPage:
    where = []
    if pedido_bling:
        where.append(BlingSituacaoAudit.pedido_bling == pedido_bling)
    if origem:
        where.append(BlingSituacaoAudit.origem == origem)
    if mudado_por:
        where.append(BlingSituacaoAudit.mudado_por == mudado_por)
    if since:
        where.append(BlingSituacaoAudit.created_at >= since)
    if until:
        where.append(BlingSituacaoAudit.created_at < until)

    total = (
        await session.execute(
            select(func.count()).select_from(BlingSituacaoAudit).where(and_(*where))
        )
    ).scalar_one()

    rows = (
        await session.execute(
            select(BlingSituacaoAudit, User.email, User.name)
            .outerjoin(User, User.id == BlingSituacaoAudit.mudado_por)
            .where(and_(*where))
            .order_by(BlingSituacaoAudit.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()

    items = [
        SituacaoAuditOut(
            id=r.id,
            created_at=r.created_at,
            pedido_bling=r.pedido_bling,
            bling_id=r.bling_id,
            sku=r.sku,
            situacao_antiga=r.situacao_antiga,
            situacao_nova=r.situacao_nova,
            origem=r.origem,
            mudado_por=r.mudado_por,
            mudado_por_email=email,
            mudado_por_nome=name,
        )
        for r, email, name in rows
    ]
    return SituacaoAuditPage(items=items, total=int(total), limit=limit, offset=offset)
