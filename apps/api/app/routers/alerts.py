"""Alerts router (Fase 6; M-19 em 23/11/2026).

M-19: cada alerta tem um `escopo` (ADM / equipe / pessoal) e a cerca de quem
vê o quê é daqui do servidor (`services/alertas_cerca.py`) — a lista, os
filtros, o número do sino e o "marcar lido". O lido é por pessoa
(`alerta_leituras`): o mesmo alerta de anúncio aparece para a equipe toda e
cada um marca o seu. Filtros, busca e paginação valem para TODOS os alertas,
não só para os mais recentes.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, distinct, false, func, literal, select
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.db import get_session
from app.deps.auth import require_active_user
from app.models import (
    Alert,
    AlertaLeitura,
    AlertType,
    BackgroundJob,
    BackgroundJobType,
    Integration,
    User,
)
from app.models.alert import ESCOPO_EQUIPE
from app.schemas.alerts import (
    AlertListOut,
    AlertOpcaoConta,
    AlertOpcoesOut,
    AlertOut,
    LastDailySyncOut,
    MarkReadOut,
    UnreadCountOut,
)
from app.services import alertas_cerca as cerca

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

Leitura = Literal["todos", "nao_lidos", "lidos"]


def _lista(valores: list[str] | None) -> set[str] | None:
    """None = sem o filtro; lista (mesmo só com '') = só o que estiver nela."""
    if valores is None:
        return None
    return {v.strip() for item in valores for v in item.split(",") if v.strip()}


async def _filtros(
    session: AsyncSession,
    user: User,
    *,
    q: str | None,
    equipes: list[str] | None,
    plataformas: list[str] | None,
    contas: list[str] | None,
    tipos: list[str] | None,
    mapa: dict[UUID, int] | None,
) -> list[ColumnElement[bool]]:
    where: list[ColumnElement[bool]] = [await cerca.clausula_visivel(session, user)]
    if q and q.strip():
        where.append(cerca.clausula_busca(q))
    eq = _lista(equipes)
    if eq is not None:
        where.append(cerca.clausula_equipes(eq, mapa or {}))
    plats = _lista(plataformas)
    if plats is not None:
        where.append(Alert.plataforma.in_(sorted(plats)) if plats else false())
    cts = _lista(contas)
    if cts is not None:
        ids = []
        for c in cts:
            try:
                ids.append(UUID(c))
            except ValueError:
                continue
        where.append(Alert.integration_id.in_(ids) if ids else false())
    tps = _lista(tipos)
    if tps is not None:
        validos = [t for t in tps if t in {e.value for e in AlertType}]
        where.append(Alert.type.in_([AlertType(t) for t in validos]) if validos else false())
    return where


def _clausula_leitura(user: User, leitura: Leitura) -> ColumnElement[bool] | None:
    if leitura == "nao_lidos":
        return cerca.nao_lido(user.id)
    if leitura == "lidos":
        return ~cerca.nao_lido(user.id)
    return None


def _saida(a: Alert, lido_em, nome_integracao: str | None, mapa: dict[UUID, int]) -> AlertOut:
    equipe = None
    if a.escopo == ESCOPO_EQUIPE:
        equipe = mapa.get(a.integration_id) if a.integration_id else a.equipe
    return AlertOut(
        id=a.id,
        type=a.type.value,
        severity=a.severity.value,
        title=a.title,
        message=a.message,
        payload=cerca.payload_publico(a.payload),
        read_at=lido_em,
        created_at=a.created_at,
        escopo=a.escopo,
        equipe=equipe,
        plataforma=a.plataforma,
        conta=nome_integracao or a.conta,
        integration_id=a.integration_id,
        tipo=cerca.tipo_do_alerta(a.type, a.dedupe_key),
    )


async def _contar(session: AsyncSession, where: list[ColumnElement[bool]]) -> int:
    return (
        await session.execute(select(func.count()).select_from(Alert).where(and_(*where)))
    ).scalar_one()


@router.get("", response_model=AlertListOut)
async def list_alerts(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    unread_only: bool = Query(False),
    leitura: Leitura = Query("todos"),
    q: str | None = Query(None, max_length=200),
    equipes: list[str] | None = Query(None),
    plataformas: list[str] | None = Query(None),
    contas: list[str] | None = Query(None),
    tipos: list[str] | None = Query(None),
) -> AlertListOut:
    mapa = await cerca.equipes_das_integracoes(session)
    where = await _filtros(
        session,
        user,
        q=q,
        equipes=equipes,
        plataformas=plataformas,
        contas=contas,
        tipos=tipos,
        mapa=mapa,
    )
    if unread_only:
        leitura = "nao_lidos"
    com_leitura = list(where)
    clausula = _clausula_leitura(user, leitura)
    if clausula is not None:
        com_leitura.append(clausula)

    stmt = (
        select(Alert, AlertaLeitura.lido_em, Integration.name)
        .outerjoin(
            AlertaLeitura,
            and_(AlertaLeitura.alert_id == Alert.id, AlertaLeitura.user_id == user.id),
        )
        .outerjoin(Integration, Integration.id == Alert.integration_id)
        .where(and_(*com_leitura))
        .order_by(Alert.created_at.desc(), Alert.id.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).all()
    total = await _contar(session, where)
    unread = await _contar(session, [*where, cerca.nao_lido(user.id)])
    encontrados = total if clausula is None else await _contar(session, com_leitura)
    return AlertListOut(
        items=[_saida(a, lido_em, nome, mapa) for a, lido_em, nome in rows],
        total=total,
        unread=unread,
        encontrados=encontrados,
    )


@router.get("/opcoes", response_model=AlertOpcoesOut)
async def opcoes(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
) -> AlertOpcoesOut:
    """As caixas da barra: Equipe ▾ (só o que a pessoa pode ver), Plataforma ▾
    e Contas ▾ (as que têm alerta que ela vê — quem é de equipe só vê as
    contas da equipe)."""
    visivel = await cerca.clausula_visivel(session, user)
    plataformas = (
        (
            await session.execute(
                select(distinct(Alert.plataforma)).where(visivel, Alert.plataforma.is_not(None))
            )
        )
        .scalars()
        .all()
    )
    contas_rows = (
        await session.execute(
            select(Integration.id, Integration.name, Integration.platform)
            .where(
                Integration.id.in_(
                    select(Alert.integration_id).where(visivel, Alert.integration_id.is_not(None))
                )
            )
            .order_by(func.lower(Integration.name))
        )
    ).all()
    mapa = await cerca.equipes_das_integracoes(session)
    return AlertOpcoesOut(
        equipes=await cerca.opcoes_de_equipe(session, user),
        plataformas=sorted(p for p in plataformas if p),
        contas=[
            AlertOpcaoConta(
                id=iid,
                nome=(nome or "").strip() or str(iid),
                plataforma=getattr(plat, "value", plat),
                equipe=mapa.get(iid),
            )
            for iid, nome, plat in contas_rows
        ],
    )


@router.get("/unread-count", response_model=UnreadCountOut)
async def unread_count(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
) -> UnreadCountOut:
    return UnreadCountOut(unread=await cerca.contar_nao_lidos(session, user))


@router.get("/last-daily-sync", response_model=LastDailySyncOut)
async def last_daily_sync(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
) -> LastDailySyncOut:
    job = (
        await session.execute(
            select(BackgroundJob)
            .where(
                BackgroundJob.created_by == user.id,
                BackgroundJob.type == BackgroundJobType.SYNC_ALL,
            )
            .order_by(BackgroundJob.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if job is None:
        return LastDailySyncOut()
    return LastDailySyncOut(
        job_id=job.id,
        status=job.status.value,
        started_at=job.started_at,
        finished_at=job.finished_at,
        total=job.total,
        processed=job.processed,
        result=job.result or {},
    )


@router.post("/{alert_id}/read", response_model=AlertOut)
async def mark_read(
    alert_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
) -> AlertOut:
    row = (
        await session.execute(
            select(Alert, Integration.name)
            .outerjoin(Integration, Integration.id == Alert.integration_id)
            .where(Alert.id == alert_id, await cerca.clausula_visivel(session, user))
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "alert_not_found"})
    a, nome = row
    await session.execute(
        pg_insert(AlertaLeitura).values(user_id=user.id, alert_id=a.id).on_conflict_do_nothing()
    )
    await session.commit()
    lido_em = (
        await session.execute(
            select(AlertaLeitura.lido_em).where(
                AlertaLeitura.alert_id == a.id, AlertaLeitura.user_id == user.id
            )
        )
    ).scalar_one()
    mapa = await cerca.equipes_das_integracoes(session) if a.escopo == ESCOPO_EQUIPE else {}
    return _saida(a, lido_em, nome, mapa)


@router.post("/read-all", response_model=MarkReadOut)
async def mark_all_read(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
    q: str | None = Query(None, max_length=200),
    equipes: list[str] | None = Query(None),
    plataformas: list[str] | None = Query(None),
    contas: list[str] | None = Query(None),
) -> MarkReadOut:
    """Marca como lidos os não lidos que a pessoa vê (com os filtros da barra,
    se vierem: o que a lista mostra)."""
    mapa = await cerca.equipes_das_integracoes(session) if equipes is not None else None
    where = await _filtros(
        session,
        user,
        q=q,
        equipes=equipes,
        plataformas=plataformas,
        contas=contas,
        tipos=None,
        mapa=mapa,
    )
    result = await session.execute(
        pg_insert(AlertaLeitura)
        .from_select(
            ["user_id", "alert_id"],
            select(literal(user.id, type_=PG_UUID(as_uuid=True)), Alert.id).where(
                *where, cerca.nao_lido(user.id)
            ),
        )
        .on_conflict_do_nothing()
    )
    await session.commit()
    return MarkReadOut(updated=result.rowcount or 0)
