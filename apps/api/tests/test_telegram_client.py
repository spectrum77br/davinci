"""TelegramClient tests (Fase 7)."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.services.telegram import TELEGRAM_API_BASE, TelegramClient, TelegramConfigError


@pytest.mark.asyncio
async def test_send_message_posts_to_bot_endpoint() -> None:
    cli = TelegramClient(bot_token="TOK", default_chat_id="GLOBAL")
    with respx.mock(assert_all_called=True) as r:
        route = r.post(f"{TELEGRAM_API_BASE}/botTOK/sendMessage").mock(
            return_value=httpx.Response(200, json={"ok": True, "result": {"id": 1}})
        )
        data = await cli.send_message("hello", chat_id="-1001")
    assert data["ok"] is True
    body = route.calls[0].request.content.decode()
    assert '"chat_id":"-1001"' in body
    assert '"text":"hello"' in body


@pytest.mark.asyncio
async def test_send_falls_back_to_default_chat() -> None:
    cli = TelegramClient(bot_token="TOK", default_chat_id="GLOBAL")
    with respx.mock(assert_all_called=True) as r:
        route = r.post(f"{TELEGRAM_API_BASE}/botTOK/sendMessage").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        await cli.send_message("hi")
    assert '"chat_id":"GLOBAL"' in route.calls[0].request.content.decode()


@pytest.mark.asyncio
async def test_missing_bot_token_raises() -> None:
    cli = TelegramClient(bot_token="", default_chat_id="X")
    with pytest.raises(TelegramConfigError):
        await cli.send_message("x")


@pytest.mark.asyncio
async def test_missing_chat_id_raises() -> None:
    cli = TelegramClient(bot_token="TOK", default_chat_id="")
    with pytest.raises(TelegramConfigError):
        await cli.send_message("x")


@pytest.mark.asyncio
async def test_safe_send_swallows_errors() -> None:
    cli = TelegramClient(bot_token="", default_chat_id="")
    ok = await cli.safe_send("x")
    assert ok is False


@pytest.mark.asyncio
async def test_telegram_api_returns_not_ok_raises() -> None:
    cli = TelegramClient(bot_token="TOK", default_chat_id="X")
    with respx.mock(assert_all_called=True) as r:
        r.post(f"{TELEGRAM_API_BASE}/botTOK/sendMessage").mock(
            return_value=httpx.Response(
                200, json={"ok": False, "description": "Bad chat id"}
            )
        )
        with pytest.raises(RuntimeError):
            await cli.send_message("x")
