"""Dev-only auth bypass for local previews.

The single endpoint `/api/dev/mock-login` ensures an `admin@local.dev` admin
user exists, issues a session JWT, and 302s to /marketing so the operator
can land on the dashboard without going through OTP. Gated by
`ENV=development AND DEV_MOCK_LOGIN=true` so it 404s anywhere else.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import User
from app.models.enums import UserRole, UserStatus
from app.security.jwt import issue_session_token

router = APIRouter(prefix="/api/dev", tags=["dev"])


def _dev_guard() -> None:
    s = get_settings()
    if s.is_prod or not s.dev_mock_login:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"code": "dev_endpoint_disabled"}
        )


@router.get("/mock-login")
async def mock_login(
    session: Annotated[AsyncSession, Depends(get_session)],
    next: str = "/marketing",
) -> RedirectResponse:
    _dev_guard()

    email = "admin@local.dev"
    user = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if user is None:
        user = User(
            open_id=f"local-{datetime.now(UTC).timestamp():.0f}",
            email=email,
            name="Local Admin",
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    elif user.status != UserStatus.ACTIVE:
        user.status = UserStatus.ACTIVE
        await session.commit()

    token, exp, _ = issue_session_token(sub=user.open_id, role=user.role.value)
    settings_ = get_settings()
    max_age = max(1, int((exp - datetime.now(UTC)).total_seconds()))
    target = next if next.startswith("/") else "/marketing"
    redirect_url = f"{settings_.app_url}{target}"
    resp = RedirectResponse(url=redirect_url, status_code=302)
    resp.set_cookie(
        key=settings_.cookie_name,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=settings_.is_prod,
        samesite="lax",
        path="/",
    )
    return resp
