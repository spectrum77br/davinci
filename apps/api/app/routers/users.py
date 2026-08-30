from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_active_user, require_admin
from app.models import User, UserRole, UserStatus
from app.schemas.permissions import Permissions
from app.schemas.users import (
    MePermissionsOut,
    PasswordSet,
    PermissionsPatch,
    UserCreate,
    UserListOut,
    UserOut,
    UserPatch,
    _normalize_sales_teams,
    _normalize_marketing_teams,
    _normalize_stock_tags,
)
from app.security.password import hash_password

_settings = get_settings()

logger = structlog.get_logger()
router = APIRouter(prefix="/api/users", tags=["users"])


def _to_out(u: User) -> UserOut:
    return UserOut(
        id=str(u.id),
        open_id=u.open_id,
        email=u.email,
        name=u.name,
        role=u.role.value,
        status=u.status.value,
        tuta=u.tuta,
        upseller=u.upseller,
        bling_login=u.bling_login,
        adspower=u.adspower,
        duoke=u.duoke,
        threema=u.threema,
        stock_tags=u.stock_tags or None,
        sales_teams=u.sales_teams or None,
        marketing_teams=u.marketing_teams or None,
        permissions=u.permissions or {},
        has_password=u.password_hash is not None,
        last_login_at=u.last_login_at,
        disabled_at=u.disabled_at,
        created_at=getattr(u, "created_at", None),
        updated_at=getattr(u, "updated_at", None),
    )


async def _count_active_admins(session: AsyncSession, exclude_id: UUID | None = None) -> int:
    stmt = select(func.count()).select_from(User).where(
        User.role == UserRole.ADMIN,
        User.disabled_at.is_(None),
    )
    if exclude_id is not None:
        stmt = stmt.where(User.id != exclude_id)
    res = await session.execute(stmt)
    return int(res.scalar_one())


@router.get("/me/permissions", response_model=MePermissionsOut)
async def my_permissions(
    user: Annotated[User, Depends(require_active_user)],
) -> MePermissionsOut:
    perms = Permissions.from_jsonb(user.permissions).to_jsonb()
    return MePermissionsOut(
        role=user.role.value,
        is_admin=user.role == UserRole.ADMIN,
        permissions=perms,
    )


@router.get("", response_model=UserListOut)
async def list_users(
    session: Annotated[AsyncSession, Depends(get_session)],
    _admin: Annotated[User, Depends(require_admin)],
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    q: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    include_disabled: bool = Query(False),
) -> UserListOut:
    stmt = select(User)
    count_stmt = select(func.count()).select_from(User)

    filters = []
    if not include_disabled:
        filters.append(User.disabled_at.is_(None))
    if q:
        like = f"%{q.lower()}%"
        filters.append(
            or_(
                func.lower(User.email).like(like),
                func.lower(func.coalesce(User.name, "")).like(like),
                func.lower(func.coalesce(User.tuta, "")).like(like),
            )
        )
    if status_filter:
        try:
            filters.append(User.status == UserStatus(status_filter))
        except ValueError:
            raise HTTPException(400, detail={"code": "invalid_status"})

    for f in filters:
        stmt = stmt.where(f)
        count_stmt = count_stmt.where(f)

    stmt = stmt.order_by(desc(User.created_at)).limit(per_page).offset((page - 1) * per_page)

    total = int((await session.execute(count_stmt)).scalar_one())
    rows = (await session.execute(stmt)).scalars().all()
    return UserListOut(
        items=[_to_out(u) for u in rows],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/{user_id}", response_model=UserOut)
async def get_user(
    user_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _admin: Annotated[User, Depends(require_admin)],
) -> UserOut:
    res = await session.execute(select(User).where(User.id == user_id))
    u = res.scalar_one_or_none()
    if u is None:
        raise HTTPException(404, detail={"code": "user_not_found"})
    return _to_out(u)


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _admin: Annotated[User, Depends(require_admin)],
) -> UserOut:
    email = body.email.lower().strip()
    open_id = f"email:{email}"

    exists = await session.execute(select(User).where(User.email == email))
    if exists.scalar_one_or_none():
        raise HTTPException(409, detail={"code": "email_exists"})

    perms = (body.permissions or Permissions.from_jsonb({})).to_jsonb()

    password_hash = None
    if body.password is not None:
        if len(body.password) < _settings.password_min_length:
            raise HTTPException(
                400,
                detail={"code": "password_too_short", "min": _settings.password_min_length},
            )
        password_hash = hash_password(body.password)

    u = User(
        open_id=open_id,
        email=email,
        name=body.name,
        password_hash=password_hash,
        tuta=body.tuta,
        upseller=body.upseller,
        bling_login=body.bling_login,
        adspower=body.adspower,
        duoke=body.duoke,
        threema=body.threema,
        stock_tags=_normalize_stock_tags(body.stock_tags),
        sales_teams=_normalize_sales_teams(body.sales_teams),
        marketing_teams=_normalize_marketing_teams(body.marketing_teams),
        role=UserRole.USER,
        status=UserStatus.PENDING,
        permissions=perms,
    )
    session.add(u)
    await session.commit()
    await session.refresh(u)
    logger.info("user_created", id=str(u.id), email=u.email)
    return _to_out(u)


@router.patch("/{user_id}", response_model=UserOut)
async def patch_user(
    user_id: UUID,
    body: UserPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin: Annotated[User, Depends(require_admin)],
) -> UserOut:
    res = await session.execute(select(User).where(User.id == user_id))
    u = res.scalar_one_or_none()
    if u is None:
        raise HTTPException(404, detail={"code": "user_not_found"})

    data = body.model_dump(exclude_unset=True)

    if "email" in data and data["email"]:
        new_email = data["email"].lower().strip()
        if new_email != u.email:
            dup = await session.execute(select(User).where(User.email == new_email))
            if dup.scalar_one_or_none():
                raise HTTPException(409, detail={"code": "email_exists"})
            u.email = new_email
            u.open_id = f"email:{new_email}"

    if "status" in data and data["status"]:
        new_status = UserStatus(data["status"])
        if u.role == UserRole.ADMIN and new_status != UserStatus.ACTIVE:
            remaining = await _count_active_admins(session, exclude_id=u.id)
            if remaining == 0:
                raise HTTPException(409, detail={"code": "last_admin"})
        u.status = new_status

    for field in ("name", "tuta", "upseller", "bling_login", "adspower", "duoke", "threema"):
        if field in data:
            setattr(u, field, data[field])

    # stock_tags: list of slugs from the whitelist. Normalisation
    # dedupes, lowercases, drops unknowns; empty list → null. Done in
    # the schema helper so create + patch share the rule.
    if "stock_tags" in data:
        u.stock_tags = _normalize_stock_tags(data["stock_tags"])

    # sales_teams: lista de números positivos. Normalisation dedupes/
    # sorts/drops non-positives; lista vazia → null.
    if "sales_teams" in data:
        u.sales_teams = _normalize_sales_teams(data["sales_teams"])

    # marketing_teams: nomes livres de equipe. Mesma semântica.
    if "marketing_teams" in data:
        u.marketing_teams = _normalize_marketing_teams(data["marketing_teams"])

    await session.commit()
    await session.refresh(u)
    return _to_out(u)


@router.patch("/{user_id}/permissions", response_model=UserOut)
async def patch_permissions(
    user_id: UUID,
    body: PermissionsPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin: Annotated[User, Depends(require_admin)],
) -> UserOut:
    res = await session.execute(select(User).where(User.id == user_id))
    u = res.scalar_one_or_none()
    if u is None:
        raise HTTPException(404, detail={"code": "user_not_found"})

    if u.role == UserRole.ADMIN and u.id != admin.id:
        raise HTTPException(403, detail={"code": "cannot_edit_admin_permissions"})

    u.permissions = body.permissions.to_jsonb()
    # JSONB columns can fail to mark dirty when the new dict compares equal
    # by identity to the previous value — force SQLAlchemy to emit the UPDATE.
    flag_modified(u, "permissions")
    await session.commit()
    await session.refresh(u)
    return _to_out(u)


@router.post("/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
async def set_user_password(
    user_id: UUID,
    body: PasswordSet,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin: Annotated[User, Depends(require_admin)],
) -> None:
    """Admin define ou reseta a senha de um usuário. Senha em claro nunca
    é logada/retornada. Um admin não pode mexer na senha de OUTRO admin
    (só na própria) — mesma regra de patch_permissions."""
    if len(body.password) < _settings.password_min_length:
        raise HTTPException(
            400,
            detail={"code": "password_too_short", "min": _settings.password_min_length},
        )

    res = await session.execute(select(User).where(User.id == user_id))
    u = res.scalar_one_or_none()
    if u is None:
        raise HTTPException(404, detail={"code": "user_not_found"})

    if u.role == UserRole.ADMIN and u.id != admin.id:
        raise HTTPException(403, detail={"code": "cannot_set_admin_password"})

    u.password_hash = hash_password(body.password)
    await session.commit()
    logger.info("user_password_set", id=str(u.id), by=str(admin.id))
    return None


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin: Annotated[User, Depends(require_admin)],
) -> None:
    if user_id == admin.id:
        raise HTTPException(409, detail={"code": "cannot_delete_self"})

    res = await session.execute(select(User).where(User.id == user_id))
    u = res.scalar_one_or_none()
    if u is None:
        raise HTTPException(404, detail={"code": "user_not_found"})
    if u.disabled_at is not None:
        return None

    if u.role == UserRole.ADMIN:
        remaining = await _count_active_admins(session, exclude_id=u.id)
        if remaining == 0:
            raise HTTPException(409, detail={"code": "last_admin"})

    u.disabled_at = datetime.now(UTC)
    u.status = UserStatus.SUSPENDED
    await session.commit()
    logger.info("user_disabled", id=str(u.id))
    return None
