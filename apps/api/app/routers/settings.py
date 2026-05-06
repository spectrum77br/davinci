"""Settings endpoints (Fase 5 partial — only webhook-url).

Fase 7 expands this router with `GET /api/settings` and `PATCH /api/settings`
for user-scoped preferences (daily_sync_time, low_stock_threshold, etc.).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.config import get_settings
from app.deps.auth import require_permission
from app.models import User

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
