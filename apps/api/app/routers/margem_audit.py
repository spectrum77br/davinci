"""Consulta da trilha de auditoria de ações na página Margem.

Lista quem (ou sistema) mudou situação, Saldo Final ou Observação de pedidos,
quando e de qual valor para qual. Só registra ações feitas PELO app — ver
`app/services/margem_audit.py`. Restrito ao dono da conta (require_owner).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_owner
from app.models import MargemAudit, User
from app.schemas.margem_audit import MargemAuditOut, MargemAuditPage

router = APIRouter(prefix="/api", tags=["margem-audit"])


@router.get("/margem-audit", response_model=MargemAuditPage)
async def list_margem_audit(
    session: Annotated[AsyncSession, Depends(get_session)],
    _owner: Annotated[User, Depends(require_owner)],
    pedido_bling: str | None = Query(None),
    acao: str | None = Query(None),
    origem: str | None = Query(None),
    mudado_por: UUID | None = Query(None),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> MargemAuditPage:
    where = []
    if pedido_bling:
        where.append(MargemAudit.pedido_bling == pedido_bling)
    if acao:
        where.append(MargemAudit.acao == acao)
    if origem:
        where.append(MargemAudit.origem == origem)
    if mudado_por:
        where.append(MargemAudit.mudado_por == mudado_por)
    if since:
        where.append(MargemAudit.created_at >= since)
    if until:
        where.append(MargemAudit.created_at < until)

    total = (
        await session.execute(
            select(func.count()).select_from(MargemAudit).where(and_(*where))
        )
    ).scalar_one()

    rows = (
        await session.execute(
            select(MargemAudit, User.email, User.name)
            .outerjoin(User, User.id == MargemAudit.mudado_por)
            .where(and_(*where))
            .order_by(MargemAudit.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()

    items = [
        MargemAuditOut(
            id=r.id,
            created_at=r.created_at,
            acao=r.acao,
            pedido_bling=r.pedido_bling,
            bling_id=r.bling_id,
            sku=r.sku,
            valor_antigo=r.valor_antigo,
            valor_novo=r.valor_novo,
            origem=r.origem,
            mudado_por=r.mudado_por,
            mudado_por_email=email,
            mudado_por_nome=name,
        )
        for r, email, name in rows
    ]
    return MargemAuditPage(items=items, total=int(total), limit=limit, offset=offset)
