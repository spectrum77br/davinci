from typing import Annotated, Literal

import jwt
from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import User, UserRole, UserStatus
from app.security.jwt import decode_session_token

_settings = get_settings()


async def get_current_user(
    session: Annotated[AsyncSession, Depends(get_session)],
    token: Annotated[str | None, Cookie(alias=_settings.cookie_name)] = None,
) -> User | None:
    if not token:
        return None
    try:
        payload = decode_session_token(token)
    except jwt.PyJWTError:
        return None
    sub = payload.get("sub")
    if not sub:
        return None
    res = await session.execute(select(User).where(User.open_id == sub))
    return res.scalar_one_or_none()


async def require_user(
    user: Annotated[User | None, Depends(get_current_user)],
) -> User:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"code": "unauthenticated"})
    return user


async def require_active_user(user: Annotated[User, Depends(require_user)]) -> User:
    if user.status != UserStatus.ACTIVE:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={"code": "not_active", "status": user.status.value},
        )
    return user


async def require_admin(user: Annotated[User, Depends(require_active_user)]) -> User:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "admin_only"})
    return user


def require_permission(resource: str, action: Literal["view", "edit", "delete"]):
    async def dep(user: Annotated[User, Depends(require_active_user)]) -> User:
        if user.role == UserRole.ADMIN:
            return user
        perm = (user.permissions or {}).get(resource, {})
        if not perm.get(action, False):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail={"code": "forbidden", "resource": resource, "action": action},
            )
        return user

    return dep
