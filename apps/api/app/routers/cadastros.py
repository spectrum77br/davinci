from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import (
    MARKETPLACES,
    Cadastro,
    CadastroStatus,
    CadastroStore,
    CadastroTipo,
    Company,
    Store,
    User,
)
from app.schemas.companies import (
    CadastroCreate,
    CadastroDetailOut,
    CadastroGridOut,
    CadastroGridRow,
    CadastroGridStoreCell,
    CadastroOut,
    CadastroPatch,
    CadastroStoreLink,
    CadastroStoresPut,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/api/cadastros", tags=["cadastros"])


def _to_tipo(v: str) -> CadastroTipo:
    try:
        return CadastroTipo(v)
    except ValueError as e:
        raise HTTPException(400, detail={"code": "tipo_invalid", "value": v}) from e


def _to_status(v: str) -> CadastroStatus:
    try:
        return CadastroStatus(v)
    except ValueError as e:
        raise HTTPException(400, detail={"code": "status_invalid", "value": v}) from e


@router.get("/grid", response_model=CadastroGridOut)
async def cadastros_grid(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("cadastro", "view"))],
) -> CadastroGridOut:
    cadastros = (await session.execute(select(Cadastro).order_by(Cadastro.tipo, Cadastro.codigo))).scalars().all()
    links = (await session.execute(select(CadastroStore))).scalars().all()
    stores = {s.id: s for s in (await session.execute(select(Store))).scalars().all()}
    companies = {c.id: c for c in (await session.execute(select(Company))).scalars().all()}

    by_cad: dict[UUID, list[CadastroStore]] = {}
    for link in links:
        by_cad.setdefault(link.cadastro_id, []).append(link)

    rows: list[CadastroGridRow] = []
    for cad in cadastros:
        cells: dict[str, list[CadastroGridStoreCell]] = {mk: [] for mk in MARKETPLACES}
        for link in by_cad.get(cad.id, []):
            s = stores.get(link.store_id)
            if not s:
                continue
            company = companies.get(s.company_id)
            cells[s.marketplace.value].append(
                CadastroGridStoreCell(
                    store_id=s.id,
                    alias=link.alias,
                    company_apelido=(s.apelido_override or (company.apelido if company else "")),
                    store_status=s.status.value,
                )
            )
        rows.append(CadastroGridRow(cadastro=CadastroOut.model_validate(cad), cells=cells))
    return CadastroGridOut(marketplaces=list(MARKETPLACES), rows=rows)


@router.get("", response_model=list[CadastroOut])
async def list_cadastros(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("cadastro", "view"))],
    tipo: str | None = Query(None),
    store_id: UUID | None = Query(None),
    search: str | None = Query(None),
) -> list[CadastroOut]:
    stmt = select(Cadastro)
    if tipo:
        stmt = stmt.where(Cadastro.tipo == _to_tipo(tipo))
    if search:
        like = f"%{search.lower()}%"
        stmt = stmt.where(or_(Cadastro.codigo.ilike(like), Cadastro.label.ilike(like)))
    if store_id:
        stmt = stmt.join(CadastroStore, CadastroStore.cadastro_id == Cadastro.id).where(
            CadastroStore.store_id == store_id
        )
    rows = (await session.execute(stmt.order_by(Cadastro.tipo, Cadastro.codigo))).scalars().all()
    return [CadastroOut.model_validate(c) for c in rows]


@router.get("/{cadastro_id}", response_model=CadastroDetailOut)
async def get_cadastro(
    cadastro_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("cadastro", "view"))],
) -> CadastroDetailOut:
    c = (await session.execute(select(Cadastro).where(Cadastro.id == cadastro_id))).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "cadastro_not_found"})
    links = (
        await session.execute(select(CadastroStore).where(CadastroStore.cadastro_id == c.id))
    ).scalars().all()
    out = CadastroDetailOut.model_validate(c)
    out.stores = [CadastroStoreLink(store_id=link.store_id, alias=link.alias) for link in links]
    return out


@router.post("", response_model=CadastroOut, status_code=status.HTTP_201_CREATED)
async def create_cadastro(
    body: CadastroCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("cadastro", "edit"))],
) -> CadastroOut:
    cad = Cadastro(
        tipo=_to_tipo(body.tipo),
        provedor=body.provedor,
        responsavel_id=body.responsavel_id,
        codigo=body.codigo,
        label=body.label,
        status=_to_status(body.status) if body.status else CadastroStatus.ACTIVE,
        obs=body.obs,
    )
    session.add(cad)
    await session.flush()
    if body.store_ids:
        for sid in body.store_ids:
            session.add(CadastroStore(cadastro_id=cad.id, store_id=sid))
    await session.commit()
    await session.refresh(cad)
    return CadastroOut.model_validate(cad)


@router.patch("/{cadastro_id}", response_model=CadastroOut)
async def patch_cadastro(
    cadastro_id: UUID,
    body: CadastroPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("cadastro", "edit"))],
) -> CadastroOut:
    c = (await session.execute(select(Cadastro).where(Cadastro.id == cadastro_id))).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "cadastro_not_found"})
    data = body.model_dump(exclude_unset=True)
    if "tipo" in data and data["tipo"] is not None:
        data["tipo"] = _to_tipo(data["tipo"])
    if "status" in data and data["status"] is not None:
        data["status"] = _to_status(data["status"])
    for k, v in data.items():
        setattr(c, k, v)
    await session.commit()
    await session.refresh(c)
    return CadastroOut.model_validate(c)


@router.delete("/{cadastro_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cadastro(
    cadastro_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("cadastro", "delete"))],
) -> None:
    c = (await session.execute(select(Cadastro).where(Cadastro.id == cadastro_id))).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "cadastro_not_found"})
    await session.delete(c)
    await session.commit()
    return None


@router.put("/{cadastro_id}/stores", response_model=CadastroDetailOut)
async def put_cadastro_stores(
    cadastro_id: UUID,
    body: CadastroStoresPut,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("cadastro", "edit"))],
) -> CadastroDetailOut:
    c = (await session.execute(select(Cadastro).where(Cadastro.id == cadastro_id))).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "cadastro_not_found"})

    desired = {link.store_id: link.alias for link in body.links}
    if desired:
        existing_stores = (
            await session.execute(select(Store.id).where(Store.id.in_(desired.keys())))
        ).scalars().all()
        if len(existing_stores) != len(desired):
            raise HTTPException(400, detail={"code": "store_not_found"})

    await session.execute(delete(CadastroStore).where(CadastroStore.cadastro_id == c.id))
    for sid, alias in desired.items():
        session.add(CadastroStore(cadastro_id=c.id, store_id=sid, alias=alias))
    await session.commit()

    out = CadastroDetailOut.model_validate(c)
    out.stores = [CadastroStoreLink(store_id=sid, alias=a) for sid, a in desired.items()]
    return out
