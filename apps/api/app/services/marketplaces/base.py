"""Common interface for every marketplace client.

Closed in Fase 4a — sub-phases 4b.ML/4b.Shopee/4b.Amazon implement, never alter
this signature. See PRD §11 Fase 4a.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from app.models import ProductLink


class SyncStatus(StrEnum):
    OK = "ok"
    SKIPPED = "skipped"
    RETRYABLE = "retryable"
    FATAL = "fatal"
    REQUIRES_REVIEW = "requires_review"


@dataclass(slots=True)
class TestResult:
    ok: bool
    detail: str | None = None
    info: dict | None = None


@dataclass(slots=True)
class SyncResult:
    status: SyncStatus
    qty_before: int | None = None
    qty_after: int | None = None
    error_code: str | None = None
    error_detail: str | None = None
    payload: dict = field(default_factory=dict)


class MarketplaceClient(Protocol):
    """Common interface across Bling/ML/Shopee/Amazon clients."""

    async def test_connection(self) -> TestResult: ...

    async def update_stock(
        self,
        link: "ProductLink",
        qty: int,
        *,
        bling_store_id: int | None = None,
        force: bool = False,
    ) -> SyncResult: ...
