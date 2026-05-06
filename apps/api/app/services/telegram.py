"""Telegram Bot API client (Fase 7).

Sends notifications via the Bot API. The bot token is global
(`TELEGRAM_BOT_TOKEN`); each user can pin a personal `telegram_chat_id` in
`user_settings`, otherwise the global `TELEGRAM_CHAT_ID` is used.
"""

from __future__ import annotations

import httpx
import structlog

from app.config import get_settings

logger = structlog.get_logger()

TELEGRAM_API_BASE = "https://api.telegram.org"


class TelegramConfigError(RuntimeError):
    """Raised when bot token or chat id is missing."""


class TelegramClient:
    def __init__(
        self,
        bot_token: str | None = None,
        default_chat_id: str | None = None,
    ) -> None:
        s = get_settings()
        self.bot_token = bot_token or s.telegram_bot_token
        self.default_chat_id = default_chat_id or s.telegram_chat_id

    def _resolve_chat(self, chat_id: str | None) -> str:
        cid = chat_id or self.default_chat_id
        if not self.bot_token:
            raise TelegramConfigError("telegram_bot_token_missing")
        if not cid:
            raise TelegramConfigError("telegram_chat_id_missing")
        return cid

    async def send_message(
        self,
        text: str,
        *,
        chat_id: str | None = None,
        parse_mode: str = "HTML",
        disable_web_page_preview: bool = True,
    ) -> dict:
        cid = self._resolve_chat(chat_id)
        url = f"{TELEGRAM_API_BASE}/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": cid,
            "text": text[:4096],
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_web_page_preview,
        }
        async with httpx.AsyncClient(timeout=15) as cli:
            resp = await cli.post(url, json=payload)
        if resp.status_code >= 400:
            logger.warning(
                "telegram_send_failed",
                status=resp.status_code,
                body=resp.text[:200],
            )
            resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"telegram_error: {data.get('description')}")
        return data

    async def safe_send(
        self, text: str, *, chat_id: str | None = None
    ) -> bool:
        """Send and swallow errors — returns True on success."""
        try:
            await self.send_message(text, chat_id=chat_id)
            return True
        except Exception as e:
            logger.warning("telegram_safe_send_failed", err=str(e))
            return False
