"""Settings endpoints (Fase 5 webhook-url + Fase 7 user preferences)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_active_user, require_permission
from app.models import User, UserSettings
from app.schemas.settings import SettingsOut, SettingsPatchIn

router = APIRouter(prefix="/api/settings", tags=["settings"])

BLING_EVENTS: list[str] = [
    "produto.alterado",
    "produto.estoque.alterado",
    "produto.criado",
]


class WebhookUrlOut(BaseModel):
    url: str
    secret_hint: str
    events: list[str]


def _hint(secret: str) -> str:
    if len(secret) >= 8:
        return f"{secret[:4]}…{secret[-4:]}"
    return "(não configurado)"


def _defaults_for(user: User) -> UserSettings:
    return UserSettings(
        user_id=user.id,
        daily_sync_enabled=False,
        daily_sync_time=None,
        sync_interval_minutes=None,
        low_stock_threshold=None,
        notify_email=True,
        notify_telegram=False,
        notify_daily_sync=True,
        telegram_chat_id=None,
    )


@router.get("/webhook-url", response_model=WebhookUrlOut)
async def webhook_url(
    _user: Annotated[User, Depends(require_permission("sincronizacoes", "view"))],
) -> WebhookUrlOut:
    s = get_settings()
    return WebhookUrlOut(
        url=f"{s.api_url.rstrip('/')}/api/webhooks/bling",
        secret_hint=_hint(s.bling_webhook_secret or ""),
        events=BLING_EVENTS,
    )


@router.get("", response_model=SettingsOut)
async def get_user_settings(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
) -> SettingsOut:
    us = await session.get(UserSettings, user.id)
    if us is None:
        us = _defaults_for(user)
    return SettingsOut.model_validate(us, from_attributes=True)


@router.patch("", response_model=SettingsOut)
async def patch_user_settings(
    body: SettingsPatchIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
) -> SettingsOut:
    us = await session.get(UserSettings, user.id)
    created = False
    if us is None:
        us = _defaults_for(user)
        session.add(us)
        created = True

    fields = body.model_dump(exclude_unset=True)
    for k, v in fields.items():
        setattr(us, k, v)

    if created:
        await session.flush()
    await session.commit()
    return SettingsOut.model_validate(us, from_attributes=True)
