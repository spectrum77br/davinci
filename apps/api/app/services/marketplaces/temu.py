"""Temu client (stub).

Stores credentials so the integration can be saved from the UI; real API
calls (test_connection, update_stock) are not yet implemented and return
deterministic not-implemented results.

Credentials shape stored in `integrations.credentials`:
    {
      "app_key":      str,
      "app_secret":   str,
      "access_token": str,
      "region":       str (default "global"),
    }
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.marketplaces.base import SyncResult, SyncStatus, TestResult

if TYPE_CHECKING:
    from app.models import ProductLink


class TemuClient:
    def __init__(self, creds: dict, on_token_refresh=None):
        self.creds = dict(creds)
        self._on_refresh = on_token_refresh

    async def test_connection(self) -> TestResult:
        required = ("app_key", "app_secret", "access_token")
        missing = [k for k in required if not self.creds.get(k)]
        if missing:
            return TestResult(ok=False, detail=f"missing_credentials: {','.join(missing)}")
        return TestResult(ok=False, detail="temu_not_implemented_yet")

    async def update_stock(
        self,
        link: ProductLink,
        qty: int,
        *,
        bling_store_id: int | None = None,
    ) -> SyncResult:
        del link, qty, bling_store_id
        return SyncResult(
            status=SyncStatus.SKIPPED,
            error_code="platform_not_implemented",
            error_detail="temu client stub",
        )
