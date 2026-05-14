from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, or_, select
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
    StoreInfo,
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
    CadastroRawLinkResolve,
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

    # Index store_info by tipo so we can also mark a cadastro as "in use" when
    # its codigo appears in `store_info.phone`/`email`/`server` of any loja —
    # even when no explicit cadastros_stores link exists. Keys are
    # (platform_lower, codigo_lower); values are the account_name to show.
    si_rows = (
        await session.execute(
            select(StoreInfo.platform, StoreInfo.account_name, StoreInfo.phone, StoreInfo.email, StoreInfo.server)
        )
    ).all()
    si_index: dict[CadastroTipo, dict[tuple[str, str], str]] = {
        CadastroTipo.FONE: {},
        CadastroTipo.EMAIL: {},
        CadastroTipo.SERVIDOR: {},
    }
    for plat, name, phone, email, server in si_rows:
        plat_l = (plat or "").strip().lower()
        nm = (name or "").strip()
        for tipo, val in (
            (CadastroTipo.FONE, phone),
            (CadastroTipo.EMAIL, email),
            (CadastroTipo.SERVIDOR, server),
        ):
            if val and plat_l:
                si_index[tipo].setdefault((plat_l, val.strip().lower()), nm or "")

    rows: list[CadastroGridRow] = []
    for cad in cadastros:
        cells: dict[str, list[CadastroGridStoreCell]] = {mk: [] for mk in MARKETPLACES}
        # Marks marketplaces already accounted for by FK link, keyed by alias
        # (lower) so we can avoid duplicating a row from store_info when the
        # same name was already linked through cadastros_stores.
        covered: dict[str, set[str]] = {mk: set() for mk in MARKETPLACES}
        for link in by_cad.get(cad.id, []):
            s = stores.get(link.store_id)
            if not s:
                continue
            company = companies.get(s.company_id)
            # Dedup key uses company.apelido (canonical) so an FK cell with
            # apelido_override "Shopee Poofy" + store_info entry "poofy" are
            # recognized as the same loja and don't render twice.
            canonical = (company.apelido if company else "").strip().lower()
            display = (company.apelido if company else "") or s.apelido_override or ""
            cells[s.marketplace.value].append(
                CadastroGridStoreCell(
                    store_id=s.id,
                    alias=link.alias,
                    company_apelido=display,
                    store_status=s.status.value,
                )
            )
            if canonical:
                covered[s.marketplace.value].add(canonical)
        # Fold in store_info-only matches for this cadastro's tipo.
        idx = si_index.get(cad.tipo, {})
        if idx:
            cod = (cad.codigo or "").strip().lower()
            for mk in MARKETPLACES:
                apelido = idx.get((mk, cod))
                if apelido is None:
                    continue
                if apelido.strip().lower() in covered[mk]:
                    continue  # already represented by an FK link
                cells[mk].append(
                    CadastroGridStoreCell(
                        store_id=None,
                        alias=None,
                        company_apelido=apelido,
                        store_status="active",
                        from_store_info=True,
                    )
                )
        rows.append(CadastroGridRow(cadastro=CadastroOut.model_validate(cad), cells=cells))
    return CadastroGridOut(marketplaces=list(MARKETPLACES), rows=rows)


_STOREINFO_FIELD_FOR_TIPO = {
    CadastroTipo.FONE:     StoreInfo.phone,
    CadastroTipo.EMAIL:    StoreInfo.email,
    CadastroTipo.SERVIDOR: StoreInfo.server,
}


@router.get("/available", response_model=list[CadastroOut])
async def list_available_cadastros(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("cadastro", "view"))],
    tipo: str,
    marketplace: str,
) -> list[CadastroOut]:
    """Cadastros (fone/email/...) of `tipo` that aren't in use on `marketplace`.

    Two ways a code is considered "in use" — either is enough to hide it:
      1. A `cadastros_stores` link points at a Store with that marketplace
         (the explicit assignment path used by /cadastros).
      2. A `store_info` row on that platform already carries this code in
         the matching field (`phone` for fone, `email` for email, `server`
         for servidor). Catches data entered directly via the Lojas page
         that never went through cadastros_stores.
    """
    cad_tipo = _to_tipo(tipo)
    busy_via_link = (
        select(CadastroStore.cadastro_id)
        .join(Store, Store.id == CadastroStore.store_id)
        .where(Store.marketplace == marketplace)
    )
    field = _STOREINFO_FIELD_FOR_TIPO.get(cad_tipo)
    busy_codigos: set[str] = set()
    if field is not None:
        rows = (
            await session.execute(
                select(field).where(
                    StoreInfo.platform == marketplace,
                    field.isnot(None),
                    field != "",
                )
            )
        ).scalars().all()
        busy_codigos = {(r or "").strip().lower() for r in rows if r}
    stmt = select(Cadastro).where(
        Cadastro.tipo == cad_tipo,
        Cadastro.status == CadastroStatus.ACTIVE,
        Cadastro.id.notin_(busy_via_link),
    )
    if busy_codigos:
        stmt = stmt.where(func.lower(Cadastro.codigo).notin_(busy_codigos))
    rows = (await session.execute(stmt.order_by(Cadastro.codigo))).scalars().all()
    return [CadastroOut.model_validate(c) for c in rows]


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


@router.post("/{cadastro_id}/stores", status_code=status.HTTP_204_NO_CONTENT)
async def add_cadastro_store_link(
    cadastro_id: UUID,
    body: CadastroStoreLink,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("cadastro", "edit"))],
) -> None:
    """Adds a single (cadastro, store) link without clobbering existing ones —
    used by the Empresas "Nova conta" flow to mark fone/email codes as
    in-use the moment a new Store is created."""
    cad = (
        await session.execute(select(Cadastro).where(Cadastro.id == cadastro_id))
    ).scalar_one_or_none()
    if cad is None:
        raise HTTPException(404, detail={"code": "cadastro_not_found"})
    s = (await session.execute(select(Store).where(Store.id == body.store_id))).scalar_one_or_none()
    if s is None:
        raise HTTPException(404, detail={"code": "store_not_found"})
    existing = (
        await session.execute(
            select(CadastroStore).where(
                CadastroStore.cadastro_id == cadastro_id,
                CadastroStore.store_id == body.store_id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(CadastroStore(
            cadastro_id=cadastro_id, store_id=body.store_id, alias=body.alias,
        ))
    else:
        existing.alias = body.alias
    await session.commit()


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


@router.post("/{cadastro_id}/raw-links/{marketplace}/resolve", response_model=CadastroOut)
async def resolve_raw_link(
    cadastro_id: UUID,
    marketplace: str,
    body: CadastroRawLinkResolve,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("cadastro", "edit"))],
) -> CadastroOut:
    c = (await session.execute(select(Cadastro).where(Cadastro.id == cadastro_id))).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "cadastro_not_found"})

    store = (
        await session.execute(select(Store).where(Store.id == body.store_id))
    ).scalar_one_or_none()
    if store is None:
        raise HTTPException(400, detail={"code": "store_not_found"})
    if store.marketplace.value != marketplace:
        raise HTTPException(
            400,
            detail={"code": "marketplace_mismatch", "expected": marketplace, "got": store.marketplace.value},
        )

    existing_link = (
        await session.execute(
            select(CadastroStore).where(
                CadastroStore.cadastro_id == c.id,
                CadastroStore.store_id == body.store_id,
            )
        )
    ).scalar_one_or_none()
    if existing_link is None:
        session.add(CadastroStore(cadastro_id=c.id, store_id=body.store_id, alias=body.alias))
    else:
        existing_link.alias = body.alias

    new_raw = dict(c.raw_links or {})
    new_raw.pop(marketplace, None)
    c.raw_links = new_raw

    await session.commit()
    await session.refresh(c)
    return CadastroOut.model_validate(c)
