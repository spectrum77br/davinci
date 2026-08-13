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


async def require_owner(user: Annotated[User, Depends(require_active_user)]) -> User:
    """Restringe ao dono da conta (spectrum77) — identificado pelo
    `owner_open_id` do settings. Mais estrito que require_admin (que cobre
    qualquer admin)."""
    if user.open_id != _settings.owner_open_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "owner_only"})
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


def require_permission_any(
    resources: tuple[str, ...], action: Literal["view", "edit", "delete"]
):
    """Como `require_permission`, mas libera se QUALQUER um dos recursos tiver a
    ação. Pra endpoints compartilhados por mais de uma tela/permissão — ex.
    `/pricing/store-info`: a tela Lojas é liberada por `lojas_info`, mas o
    endpoint nasceu na Tabela de Preços (`tabela_precos`) e usuários antigos só
    têm essa. O 403 reporta o primeiro recurso da tupla (o "principal")."""

    async def dep(user: Annotated[User, Depends(require_active_user)]) -> User:
        if user.role == UserRole.ADMIN:
            return user
        perms = user.permissions or {}
        for resource in resources:
            if (perms.get(resource) or {}).get(action, False):
                return user
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "resource": resources[0], "action": action},
        )

    return dep


def user_scope(model, user):
    """SQLAlchemy where-predicate.

    Fase 1 (escopo por equipe): admin e usuário sem equipe veem tudo.
    Usuário com equipe só enxerga lojas (store_info) cuja sales_team está
    entre user.sales_teams. Demais tabelas seguem sem filtro (Fase 2
    estende módulo a módulo).
    """
    from sqlalchemy import true

    from app.models.pricing import StoreInfo  # import tardio evita ciclo

    # Admin vê tudo.
    if user.role == UserRole.ADMIN:
        return true()

    teams = user.sales_teams or []

    # Escopo por equipe só na tabela de Lojas, por enquanto.
    if model is StoreInfo and teams:
        return StoreInfo.sales_team.in_(teams)

    # Sem equipe, ou qualquer outra tabela: comportamento atual (vê tudo).
    return true()
