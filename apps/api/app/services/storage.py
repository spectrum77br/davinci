"""Local filesystem storage for user uploads (Fase 10).

Used by the audit-by-spreadsheet flow to persist xlsx files outside Postgres.
Files live under `${UPLOADS_DIR}/{user_id}/{uuid}.xlsx` where `UPLOADS_DIR`
is bind-mounted from the host (PRD §13).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID, uuid4

import aiofiles
import structlog

from app.config import get_settings

logger = structlog.get_logger()


class LocalStorage:
    def __init__(self, base_dir: str | None = None) -> None:
        self._base = Path(base_dir or get_settings().uploads_dir)

    def _user_dir(self, user_id: UUID) -> Path:
        return self._base / str(user_id)

    async def save(self, *, user_id: UUID, content: bytes, suffix: str = ".xlsx") -> Path:
        d = self._user_dir(user_id)
        await asyncio.to_thread(d.mkdir, parents=True, exist_ok=True)
        if not suffix.startswith("."):
            suffix = "." + suffix
        path = d / f"{uuid4().hex}{suffix}"
        async with aiofiles.open(path, "wb") as f:
            await f.write(content)
        logger.info("storage_save", user_id=str(user_id), path=str(path), size=len(content))
        return path

    async def read(self, path: str | Path) -> bytes:
        async with aiofiles.open(path, "rb") as f:
            return await f.read()

    async def delete(self, path: str | Path) -> None:
        p = Path(path)
        await asyncio.to_thread(_unlink_safe, p)


def _unlink_safe(p: Path) -> None:
    try:
        p.unlink()
    except FileNotFoundError:
        pass
