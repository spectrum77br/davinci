from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class TestResult:
    ok: bool
    detail: str | None = None
    info: dict | None = None


class MarketplaceClient(Protocol):
    """Common interface across Bling/ML/Shopee/Amazon clients."""

    async def test_connection(self) -> TestResult: ...
