from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, select, text
from sqlalchemy import delete as sa_delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import (
    Cadastro,
    CadastroStore,
    CadastroTipo,
    Company,
    Integration,
    Marketplace,
    Store,
    StoreInfo,
    StoreStatus,
    User,
)
from app.schemas.companies import StoreAccountCreate, StoreCreate, StoreOut, StorePatch
from app.services.cadastro_availability import (
    available_cadastros,
    normalize_cadastro_code,
    ocupacao_global,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/api/stores", tags=["stores"])


def _to_marketplace(v: str) -> Marketplace:
    try:
        return Marketplace(v)
    except ValueError as e:
        raise HTTPException(400, detail={"code": "marketplace_invalid", "value": v}) from e


def _to_status(v: str) -> StoreStatus:
    try:
        return StoreStatus(v)
    except ValueError as e:
        raise HTTPException(400, detail={"code": "status_invalid", "value": v}) from e


@router.get("", response_model=list[StoreOut])
async def list_stores(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("empresa", "view"))],
    company_id: UUID | None = Query(None),
    marketplace: str | None = Query(None),
    status_q: str | None = Query(None, alias="status"),
) -> list[StoreOut]:
    stmt = select(Store)
    if company_id:
        stmt = stmt.where(Store.company_id == company_id)
    if marketplace:
        stmt = stmt.where(Store.marketplace == _to_marketplace(marketplace))
    if status_q:
        stmt = stmt.where(Store.status == _to_status(status_q))
    rows = (await session.execute(stmt)).scalars().all()
    return [StoreOut.model_validate(s) for s in rows]


@router.post("", response_model=StoreOut, status_code=status.HTTP_201_CREATED)
async def create_store(
    body: StoreCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("empresa", "edit"))],
) -> StoreOut:
    company = (
        await session.execute(select(Company).where(Company.id == body.company_id))
    ).scalar_one_or_none()
    if company is None:
        raise HTTPException(404, detail={"code": "company_not_found"})
    mk = _to_marketplace(body.marketplace)
    # Gate: the marketplace has to be in `companies.enabled_marketplaces`. The
    # default value seeded by 0033 contains every canonical marketplace, so
    # legacy companies are unaffected; toggling one off makes the "+" cell
    # render as a red X on the frontend and rejects writes here.
    enabled = company.enabled_marketplaces or []
    if mk.value not in enabled:
        raise HTTPException(
            403,
            detail={"code": "marketplace_not_enabled", "marketplace": mk.value},
        )
    s = Store(
        company_id=body.company_id,
        marketplace=mk,
        apelido_override=body.apelido_override,
        status=_to_status(body.status) if body.status else StoreStatus.PENDING,
        notes=body.notes,
        bling_store_id=body.bling_store_id,
    )
    session.add(s)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        if "uq_stores_company_id_marketplace" in str(e.orig):
            raise HTTPException(409, detail={"code": "store_already_exists"}) from e
        raise
    await session.refresh(s)
    logger.info("store_created", id=str(s.id), company_id=str(s.company_id), marketplace=s.marketplace.value)
    return StoreOut.model_validate(s)


@router.post("/account", response_model=StoreOut, status_code=status.HTTP_201_CREATED)
async def create_store_account(
    body: StoreAccountCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("empresa", "edit"))],
    _cad: Annotated[User, Depends(require_permission("cadastro", "edit"))],
    _info: Annotated[User, Depends(require_permission("lojas_info", "edit"))],
) -> StoreOut:
    """Cria conta, dados da loja e vínculos em uma única transação."""
    mk = _to_marketplace(body.marketplace)
    company = (await session.execute(
        select(Company).where(Company.id == body.company_id).with_for_update()
    )).scalar_one_or_none()
    if company is None:
        raise HTTPException(404, detail={"code": "company_not_found"})
    if mk.value not in (company.enabled_marketplaces or []):
        raise HTTPException(403, detail={"code": "marketplace_not_enabled"})
    existing = (await session.execute(select(Store.id).where(
        Store.company_id == company.id, Store.marketplace == mk,
    ))).scalar_one_or_none()
    # A matriz também reconhece lojas cadastradas diretamente em StoreInfo.
    platforms = (
        ("ml", "mercadolivre", "mercado livre") if mk == Marketplace.ML else (mk.value,)
    )
    store_names = (await session.execute(select(StoreInfo.account_name).where(
        func.lower(func.trim(StoreInfo.platform)).in_(platforms)
    ))).scalars().all()
    account_key = "".join(company.apelido.split()).lower()
    if existing is not None or any(
        "".join((name or "").split()).lower() == account_key for name in store_names
    ):
        raise HTTPException(409, detail={"code": "store_already_exists"})

    selection = {
        CadastroTipo.FONE: body.phone_id,
        CadastroTipo.EMAIL: body.email_id,
        CadastroTipo.SERVIDOR: body.server_id,
    }
    rows = (await session.execute(
        select(Cadastro).where(Cadastro.id.in_(selection.values()))
        .order_by(Cadastro.id).with_for_update()
    )).scalars().all()
    by_id = {row.id: row for row in rows}
    for tipo, cadastro_id in selection.items():
        row = by_id.get(cadastro_id)
        if row is None or row.tipo != tipo:
            raise HTTPException(
                409, detail={"code": "cadastro_unavailable", "tipo": tipo.value}
            )

    # Serializa o mesmo recurso no marketplace, inclusive códigos duplicados
    # em cadastros diferentes. Telefone e e-mail têm reserva independente em
    # outro marketplace; servidor (perfil do AdsPower) é único em todos eles, e
    # por isso a chave dele ignora o marketplace — senão duas criações
    # simultâneas em plataformas diferentes passariam pelo mesmo servidor.
    lock_keys = sorted(
        "store-account:{}:{}:{}".format(
            "*" if ocupacao_global(tipo) else mk.value,
            tipo.value,
            normalize_cadastro_code(by_id[cid].codigo),
        )
        for tipo, cid in selection.items()
    )
    for key in lock_keys:
        await session.execute(text(
            "SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"
        ), {"key": key})
    for tipo, cadastro_id in selection.items():
        available = await available_cadastros(session, tipo, mk)
        if cadastro_id not in {c.id for c in available}:
            raise HTTPException(
                409, detail={"code": "cadastro_unavailable", "tipo": tipo.value}
            )

    store = Store(company_id=company.id, marketplace=mk, status=StoreStatus.ACTIVE)
    session.add(store)
    await session.flush()
    session.add(StoreInfo(
        user_id=user.id, platform=mk.value, account_name=company.apelido,
        phone=by_id[body.phone_id].codigo.strip(),
        email=by_id[body.email_id].codigo.strip(),
        server=by_id[body.server_id].codigo.strip(),
    ))
    for cadastro_id in selection.values():
        session.add(CadastroStore(
            cadastro_id=cadastro_id, store_id=store.id, alias=company.apelido,
        ))
    await session.commit()
    await session.refresh(store)
    logger.info("store_account_created", id=str(store.id), marketplace=mk.value)
    return StoreOut.model_validate(store)


@router.patch("/{store_id}", response_model=StoreOut)
async def patch_store(
    store_id: UUID,
    body: StorePatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("empresa", "edit"))],
) -> StoreOut:
    s = (await session.execute(select(Store).where(Store.id == store_id))).scalar_one_or_none()
    if s is None:
        raise HTTPException(404, detail={"code": "store_not_found"})
    data = body.model_dump(exclude_unset=True)
    if "status" in data and data["status"] is not None:
        data["status"] = _to_status(data["status"])
    prev_integration_id = s.integration_id
    for k, v in data.items():
        setattr(s, k, v)
    # Mirror the link on the integration side so projections that read
    # integrations.store_id (and any reverse joins) stay in sync.
    if "integration_id" in data and data["integration_id"] != prev_integration_id:
        if prev_integration_id is not None:
            old = (
                await session.execute(select(Integration).where(Integration.id == prev_integration_id))
            ).scalar_one_or_none()
            if old is not None and old.store_id == s.id:
                old.store_id = None
        if data["integration_id"] is not None:
            new = (
                await session.execute(select(Integration).where(Integration.id == data["integration_id"]))
            ).scalar_one_or_none()
            if new is None:
                raise HTTPException(404, detail={"code": "integration_not_found"})
            new.store_id = s.id
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        if "uq_stores_integration_id" in str(e.orig):
            raise HTTPException(409, detail={"code": "integration_already_linked"}) from e
        if "uq_integrations_store_id" in str(e.orig):
            raise HTTPException(409, detail={"code": "store_already_linked"}) from e
        raise
    await session.refresh(s)
    return StoreOut.model_validate(s)


@router.delete("/{store_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_store(
    store_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("empresa", "delete"))],
) -> None:
    """Removes the Store + every shadow record tied to it.

    - `cadastros_stores` rows are dropped automatically by the FK cascade.
    - `store_info` is loosely coupled (matched by `(platform, account_name)`,
      not an FK) so we delete by hand: every store_info row whose platform
      matches the Store's marketplace and whose `account_name` (lowered)
      equals the company's apelido (lowered) is removed.
    """
    s = (await session.execute(select(Store).where(Store.id == store_id))).scalar_one_or_none()
    if s is None:
        raise HTTPException(404, detail={"code": "store_not_found"})
    company = (
        await session.execute(select(Company).where(Company.id == s.company_id))
    ).scalar_one_or_none()
    if company is not None and company.apelido:
        await session.execute(
            sa_delete(StoreInfo).where(
                and_(
                    StoreInfo.platform == s.marketplace.value,
                    func.lower(StoreInfo.account_name) == company.apelido.strip().lower(),
                )
            )
        )
    await session.delete(s)
    await session.commit()
    return None


@router.post("/{store_id}/unlink-integration", response_model=StoreOut)
async def unlink_integration(
    store_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("empresa", "edit"))],
) -> StoreOut:
    s = (await session.execute(select(Store).where(Store.id == store_id))).scalar_one_or_none()
    if s is None:
        raise HTTPException(404, detail={"code": "store_not_found"})
    if s.integration_id is not None:
        old = (
            await session.execute(select(Integration).where(Integration.id == s.integration_id))
        ).scalar_one_or_none()
        if old is not None and old.store_id == s.id:
            old.store_id = None
    s.integration_id = None
    await session.commit()
    await session.refresh(s)
    return StoreOut.model_validate(s)
