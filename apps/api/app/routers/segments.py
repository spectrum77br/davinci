from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import Segment, SegmentSpecialDate, User
from app.schemas.segments import (
    SegmentCreate,
    SegmentOut,
    SegmentPatch,
    SegmentSpecialDateCreate,
    SegmentSpecialDateOut,
    SegmentTreeNode,
    _slugify,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/api/segments", tags=["segments"])


async def _get_or_404(session: AsyncSession, sid: UUID) -> Segment:
    s = (await session.execute(select(Segment).where(Segment.id == sid))).scalar_one_or_none()
    if s is None:
        raise HTTPException(404, detail={"code": "segment_not_found"})
    return s


async def _parent_of(session: AsyncSession, sid: UUID) -> UUID | None:
    res = await session.execute(select(Segment.parent_id).where(Segment.id == sid))
    return res.scalar_one_or_none()


async def _ancestors(session: AsyncSession, sid: UUID) -> set[UUID]:
    """Returns the set of ancestor IDs (excluding `sid` itself). Used to
    block cycles when re-parenting."""
    seen: set[UUID] = set()
    cur = await _parent_of(session, sid)
    while cur is not None and cur not in seen:
        seen.add(cur)
        cur = await _parent_of(session, cur)
    return seen


@router.get("", response_model=list[SegmentOut])
async def list_segments(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("segmentos", "view"))],
) -> list[SegmentOut]:
    rows = (
        await session.execute(
            select(Segment).order_by(Segment.sort_order, Segment.name)
        )
    ).scalars().all()
    return [SegmentOut.model_validate(r) for r in rows]


@router.get("/tree", response_model=list[SegmentTreeNode])
async def segments_tree(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("segmentos", "view"))],
) -> list[SegmentTreeNode]:
    rows = (
        await session.execute(
            select(Segment).order_by(Segment.sort_order, Segment.name)
        )
    ).scalars().all()
    by_id: dict[UUID, SegmentTreeNode] = {
        r.id: SegmentTreeNode.model_validate(r) for r in rows
    }
    roots: list[SegmentTreeNode] = []
    for r in rows:
        node = by_id[r.id]
        if r.parent_id and r.parent_id in by_id:
            by_id[r.parent_id].children.append(node)
        else:
            roots.append(node)
    return roots


@router.post("", response_model=SegmentOut, status_code=status.HTTP_201_CREATED)
async def create_segment(
    body: SegmentCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("segmentos", "edit"))],
) -> SegmentOut:
    if body.parent_id is not None:
        await _get_or_404(session, body.parent_id)
    slug = body.slug or _slugify(body.name)
    if not slug:
        raise HTTPException(400, detail={"code": "slug_invalid"})
    seg = Segment(
        user_id=user.id,
        parent_id=body.parent_id,
        name=body.name,
        slug=slug,
        sort_order=body.sort_order,
        active=body.active,
    )
    session.add(seg)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise HTTPException(409, detail={"code": "segment_slug_conflict"}) from e
    await session.refresh(seg)
    return SegmentOut.model_validate(seg)


@router.patch("/{segment_id}", response_model=SegmentOut)
async def patch_segment(
    segment_id: UUID,
    body: SegmentPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("segmentos", "edit"))],
) -> SegmentOut:
    seg = await _get_or_404(session, segment_id)
    data = body.model_dump(exclude_unset=True)

    if "parent_id" in data:
        new_parent = data["parent_id"]
        if new_parent == segment_id:
            raise HTTPException(400, detail={"code": "segment_parent_self"})
        if new_parent is not None:
            await _get_or_404(session, new_parent)
            ancestors_of_new = await _ancestors(session, new_parent)
            ancestors_of_new.add(new_parent)
            # walk descendants of `seg` and ensure new_parent is not among them
            descendants = await _descendants(session, segment_id)
            if new_parent in descendants:
                raise HTTPException(400, detail={"code": "segment_parent_cycle"})

    for k, v in data.items():
        setattr(seg, k, v)

    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise HTTPException(409, detail={"code": "segment_slug_conflict"}) from e
    await session.refresh(seg)
    return SegmentOut.model_validate(seg)


async def _descendants(session: AsyncSession, sid: UUID) -> set[UUID]:
    out: set[UUID] = set()
    frontier = {sid}
    while frontier:
        children = (
            await session.execute(
                select(Segment.id).where(Segment.parent_id.in_(frontier))
            )
        ).scalars().all()
        new = set(children) - out
        if not new:
            break
        out.update(new)
        frontier = new
    return out


@router.delete("/{segment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_segment(
    segment_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("segmentos", "delete"))],
) -> None:
    seg = await _get_or_404(session, segment_id)
    await session.delete(seg)
    await session.commit()
    return None


# ============================================================ Datas Especiais
# Eduardo (01/09/2026): "vamos colocar um novo campo chamado datas especiais,
# que é a regra que vamos aprovar, para exceção, por exemplo está com margem
# negativa, aprova". Janela em que a margem baixa não trava pedidos do
# segmento (nem dos descendentes). A regra em si é aplicada na triagem da
# Margem (_MARGEM_DATA_ESPECIAL_SQL em routers/margens.py); aqui é só o CRUD.
# Sem PATCH de propósito: editar = remover + adicionar (janelas são curtas).


@router.post(
    "/{segment_id}/special-dates",
    response_model=SegmentSpecialDateOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_special_date(
    segment_id: UUID,
    body: SegmentSpecialDateCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("segmentos", "edit"))],
) -> SegmentSpecialDateOut:
    await _get_or_404(session, segment_id)
    sd = SegmentSpecialDate(
        segment_id=segment_id,
        date_start=body.date_start,
        date_end=body.date_end,
        min_margin=body.min_margin,
    )
    session.add(sd)
    await session.commit()
    await session.refresh(sd)
    logger.info(
        "segment_special_date_created",
        segment_id=str(segment_id),
        date_start=str(body.date_start),
        date_end=str(body.date_end),
        min_margin=None if body.min_margin is None else float(body.min_margin),
    )
    return SegmentSpecialDateOut.model_validate(sd)


@router.delete(
    "/{segment_id}/special-dates/{special_date_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_special_date(
    segment_id: UUID,
    special_date_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("segmentos", "edit"))],
) -> None:
    sd = (
        await session.execute(
            select(SegmentSpecialDate).where(
                SegmentSpecialDate.id == special_date_id,
                SegmentSpecialDate.segment_id == segment_id,
            )
        )
    ).scalar_one_or_none()
    if sd is None:
        raise HTTPException(404, detail={"code": "special_date_not_found"})
    await session.delete(sd)
    await session.commit()
    logger.info(
        "segment_special_date_deleted",
        segment_id=str(segment_id),
        special_date_id=str(special_date_id),
    )
    return None
