"""Admin metrics endpoint — Phase 13.

Exposes the per-marketplace counters/latencies tracked in Redis. Admin-only.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.deps.auth import require_admin
from app.models import User
from app.services import metrics

router = APIRouter(prefix="/api", tags=["metrics"])


@router.get("/metrics")
async def get_metrics(_: Annotated[User, Depends(require_admin)]) -> dict:
    return await metrics.snapshot()


@router.post("/metrics/reset", status_code=204)
async def reset_metrics(_: Annotated[User, Depends(require_admin)]) -> None:
    await metrics.reset()
