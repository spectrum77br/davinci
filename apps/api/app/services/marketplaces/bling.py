"""Bling API v3 client.

OAuth2 (authorization code), automatic refresh on 401.
Credentials stored in `integrations.credentials` shape:
    {
      "client_id": str,
      "client_secret": str,
      "access_token": str,
      "refresh_token": str,
      "token_type": "Bearer",
      "scope": str,
      "expires_at": int (epoch seconds),
    }
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog

from app.config import get_settings
from app.services.marketplaces.base import TestResult

logger = structlog.get_logger()

BLING_AUTH_URL = "https://www.bling.com.br/Api/v3/oauth/authorize"
BLING_TOKEN_URL = "https://www.bling.com.br/Api/v3/oauth/token"
BLING_API_BASE = "https://www.bling.com.br/Api/v3"


class BlingClient:
    def __init__(
        self,
        creds: dict,
        on_token_refresh=None,
    ):
        self.creds = dict(creds)
        self._on_refresh = on_token_refresh

    @property
    def access_token(self) -> str | None:
        return self.creds.get("access_token")

    @property
    def expires_at(self) -> int:
        return int(self.creds.get("expires_at") or 0)

    def _expired(self, skew: int = 30) -> bool:
        return self.expires_at - skew <= int(time.time())

    @staticmethod
    def authorize_url(state: str) -> str:
        s = get_settings()
        params = {
            "response_type": "code",
            "client_id": s.bling_client_id,
            "state": state,
            "redirect_uri": s.bling_redirect_uri,
        }
        return f"{BLING_AUTH_URL}?{urlencode(params)}"

    @staticmethod
    async def exchange_code(code: str) -> dict:
        s = get_settings()
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(
                BLING_TOKEN_URL,
                auth=(s.bling_client_id, s.bling_client_secret),
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": s.bling_redirect_uri,
                },
            )
            r.raise_for_status()
            return _normalize_token(r.json())

    async def refresh(self) -> None:
        rt = self.creds.get("refresh_token")
        if not rt:
            raise RuntimeError("missing refresh_token")
        s = get_settings()
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(
                BLING_TOKEN_URL,
                auth=(s.bling_client_id, s.bling_client_secret),
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"grant_type": "refresh_token", "refresh_token": rt},
            )
            r.raise_for_status()
            self.creds.update(_normalize_token(r.json(), prev=self.creds))
        if self._on_refresh:
            await self._on_refresh(self.creds)

    async def _request(
        self, method: str, path: str, *, params: dict | None = None, json: Any = None
    ) -> httpx.Response:
        if self._expired():
            await self.refresh()
        url = f"{BLING_API_BASE}{path}"
        headers = {"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"}
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.request(method, url, headers=headers, params=params, json=json)
        if r.status_code == 401:
            await self.refresh()
            headers["Authorization"] = f"Bearer {self.access_token}"
            async with httpx.AsyncClient(timeout=30.0) as c:
                r = await c.request(method, url, headers=headers, params=params, json=json)
        return r

    async def list_lojas(self) -> list[dict]:
        r = await self._request("GET", "/lojas")
        r.raise_for_status()
        body = r.json()
        return body.get("data", [])

    async def test_connection(self) -> TestResult:
        try:
            r = await self._request("GET", "/usuarios/me")
            if r.status_code == 200:
                return TestResult(ok=True, info=r.json().get("data"))
            return TestResult(ok=False, detail=f"status={r.status_code} body={r.text[:200]}")
        except httpx.HTTPError as e:
            return TestResult(ok=False, detail=f"http_error: {e}")
        except Exception as e:  # noqa: BLE001
            return TestResult(ok=False, detail=f"error: {e}")


def _normalize_token(payload: dict, prev: dict | None = None) -> dict:
    """Convert Bling token response to internal credential dict."""
    expires_in = int(payload.get("expires_in") or 21600)
    out: dict = dict(prev or {})
    out["access_token"] = payload["access_token"]
    if "refresh_token" in payload:
        out["refresh_token"] = payload["refresh_token"]
    out["token_type"] = payload.get("token_type", "Bearer")
    out["scope"] = payload.get("scope", out.get("scope", ""))
    out["expires_at"] = int(time.time()) + expires_in
    out["_obtained_at"] = datetime.now(UTC).isoformat()
    return out
