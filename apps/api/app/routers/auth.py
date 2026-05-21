import re
from datetime import UTC, datetime, timedelta
from typing import Annotated

import structlog
from arq.connections import ArqRedis
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.auth import get_current_user
from app.models import AuthCode, User, UserRole, UserStatus
from app.security.jwt import issue_session_token
from app.security.otp import (
    generate_code,
    generate_nonce,
    generate_prefix,
    hash_code,
    verify_code,
)
from app.services.rate_limit import RateLimitError, sliding_window_check
from app.services.turnstile import verify_turnstile
from app.worker_pool import get_arq_pool

logger = structlog.get_logger()
_settings = get_settings()
NONCE_COOKIE = "otp_nonce"
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RequestOtpBody(BaseModel):
    email: EmailStr
    turnstile_token: str | None = None


class RequestOtpResp(BaseModel):
    prefix: str
    expires_at: datetime


class VerifyOtpBody(BaseModel):
    email: EmailStr
    code: str = Field(min_length=4, max_length=16)


class UserOut(BaseModel):
    id: str
    open_id: str
    email: str
    name: str | None
    role: str
    status: str
    permissions: dict
    # Operator-of-stock tag — front-end uses this in auth.global to
    # detect "operador" users and lock them to /controle-estoque.
    stock_tag: str | None = None


class VerifyOtpResp(BaseModel):
    user: UserOut
    requires_approval: bool


def _client_ip(req: Request) -> str | None:
    fwd = req.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return req.client.host if req.client else None


def _set_session_cookie(resp: Response, token: str, exp: datetime) -> None:
    max_age = max(1, int((exp - datetime.now(UTC)).total_seconds()))
    resp.set_cookie(
        key=_settings.cookie_name,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=_settings.is_prod,
        samesite="lax",
        path="/",
        domain=_settings.cookie_domain or None,
    )


def _set_nonce_cookie(resp: Response, nonce: str, ttl_seconds: int) -> None:
    # SameSite=Lax (not Strict) so cookie persists across cross-port fetches in dev
    # (e.g. web :3000 → api :8001). Lax still blocks third-party CSRF.
    resp.set_cookie(
        key=NONCE_COOKIE,
        value=nonce,
        max_age=ttl_seconds,
        httponly=True,
        secure=_settings.is_prod,
        samesite="lax",
        path="/",
    )


def _clear_cookie(resp: Response, name: str) -> None:
    resp.delete_cookie(name, path="/")


@router.post("/request", response_model=RequestOtpResp)
async def request_otp(
    body: RequestOtpBody,
    req: Request,
    resp: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    arq: Annotated[ArqRedis, Depends(get_arq_pool)],
) -> RequestOtpResp:
    email = body.email.lower().strip()
    ip = _client_ip(req)
    ua = req.headers.get("user-agent")

    if not EMAIL_RE.match(email):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": "invalid_email"})

    if not await verify_turnstile(body.turnstile_token, remote_ip=ip):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": "turnstile_failed"})

    try:
        if ip:
            await sliding_window_check(
                key=f"otp:rl:ip:{ip}",
                limit=_settings.otp_rate_per_ip,
                window_seconds=3600,
            )
        await sliding_window_check(
            key=f"otp:rl:email:{email}",
            limit=_settings.otp_rate_per_email,
            window_seconds=3600,
        )
    except RateLimitError as e:
        resp.headers["Retry-After"] = str(e.retry_after)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "rate_limited", "retry_after": e.retry_after},
        ) from None

    code = generate_code()
    prefix = generate_prefix()
    nonce = generate_nonce()
    ttl_ms = _settings.otp_code_ttl_ms
    expires_at = datetime.now(UTC) + timedelta(milliseconds=ttl_ms)

    auth_code = AuthCode(
        email=email,
        code_hash=hash_code(code),
        prefix=prefix,
        session_nonce=nonce,
        expires_at=expires_at,
        ip=ip,
        user_agent=(ua or "")[:512] or None,
    )
    session.add(auth_code)
    await session.commit()

    _set_nonce_cookie(resp, nonce, ttl_seconds=int(ttl_ms / 1000))

    await arq.enqueue_job(
        "send_otp_email",
        email=email,
        prefix=prefix,
        code=code,
        ttl_minutes=int(ttl_ms / 60000),
    )

    logger.info("otp_requested", email=email, prefix=prefix, ip=ip)
    return RequestOtpResp(prefix=prefix, expires_at=expires_at)


@router.post("/verify", response_model=VerifyOtpResp)
async def verify_otp(
    body: VerifyOtpBody,
    req: Request,
    resp: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    nonce_cookie: Annotated[str | None, Cookie(alias=NONCE_COOKIE)] = None,
) -> VerifyOtpResp:
    email = body.email.lower().strip()
    code_input = body.code.strip().upper()

    res = await session.execute(
        select(AuthCode)
        .where(
            AuthCode.email == email,
            AuthCode.consumed_at.is_(None),
            AuthCode.expires_at > datetime.now(UTC),
        )
        .order_by(desc(AuthCode.created_at))
        .limit(1)
    )
    row = res.scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"code": "code_not_found"})

    if not nonce_cookie or nonce_cookie != row.session_nonce:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"code": "nonce_mismatch"})

    if row.attempts >= _settings.otp_max_attempts:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "too_many_attempts"},
        )

    row.attempts += 1
    if not verify_code(code_input, row.code_hash):
        await session.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"code": "code_invalid"})

    row.consumed_at = datetime.now(UTC)

    open_id = f"email:{email}"
    is_owner = open_id == _settings.owner_open_id

    user_res = await session.execute(select(User).where(User.email == email))
    user = user_res.scalar_one_or_none()
    if user is None:
        user = User(
            open_id=open_id,
            email=email,
            role=UserRole.ADMIN if is_owner else UserRole.USER,
            status=UserStatus.ACTIVE if is_owner else UserStatus.PENDING,
            permissions={},
        )
        session.add(user)
        await session.flush()
    else:
        if is_owner and user.role != UserRole.ADMIN:
            user.role = UserRole.ADMIN
            user.status = UserStatus.ACTIVE
        if user.status == UserStatus.SUSPENDED:
            await session.commit()
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "suspended"})

    user.last_login_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(user)

    token, exp, _jti = issue_session_token(sub=user.open_id, role=user.role.value)
    _set_session_cookie(resp, token, exp)
    _clear_cookie(resp, NONCE_COOKIE)

    logger.info("otp_verified", email=email, role=user.role.value, status=user.status.value)

    return VerifyOtpResp(
        user=UserOut(
            id=str(user.id),
            open_id=user.open_id,
            email=user.email,
            name=user.name,
            role=user.role.value,
            status=user.status.value,
            permissions=user.permissions,
            stock_tag=user.stock_tag,
        ),
        requires_approval=user.status == UserStatus.PENDING,
    )


@router.post("/resend")
async def resend_otp(
    body: RequestOtpBody,
    req: Request,
    resp: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    arq: Annotated[ArqRedis, Depends(get_arq_pool)],
) -> RequestOtpResp:
    return await request_otp(body, req, resp, session, arq)


@router.post("/logout")
async def logout(resp: Response) -> dict:
    _clear_cookie(resp, _settings.cookie_name)
    _clear_cookie(resp, NONCE_COOKIE)
    return {"ok": True}


@router.get("/me", response_model=UserOut | None)
async def me(user: Annotated[User | None, Depends(get_current_user)]) -> UserOut | None:
    if user is None:
        return None
    return UserOut(
        id=str(user.id),
        open_id=user.open_id,
        email=user.email,
        name=user.name,
        role=user.role.value,
        status=user.status.value,
        permissions=user.permissions,
        stock_tag=user.stock_tag,
    )
