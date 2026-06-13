"""Export assíncrono de NF-e (xlsx/zip) como BackgroundJob.

O export síncrono (`/api/notas-fiscais/export.{xlsx,xml}`) é limitado a
MAX_XML_NOTAS (500) porque cada nota custa 1 chamada de detalhe no Bling a
5 req/s globais — acima disso o request passa de ~2 min e o proxy derruba
com 502. Este job roda no worker (sem proxy no caminho) e respeita o mesmo
rate gate distribuído, então aguenta lotes grandes; o teto é só pra caber
no job_timeout do worker.

Reusa a lógica de fetch/build do router `notas_fiscais` (fonte única de
verdade do layout NF-e Report); o arquivo gerado vai pra `/data/uploads`
(volume compartilhado api↔worker) e é servido pelo endpoint de download.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import BackgroundJob, BackgroundJobStatus
from app.routers.notas_fiscais import (
    MAX_EXPORT_NOTAS_ASYNC,
    XLSX_MEDIA,
    ZIP_MEDIA,
    _fetch_all,
    _validate_range,
    _xlsx_fname,
    _xml_zip_fname,
    build_xlsx,
    build_xml_zip,
)

logger = structlog.get_logger()

# Commit periódico do progresso (processed/heartbeat). Cada nota é uma
# chamada de rede, então 25 ≈ alguns segundos entre commits.
_PROGRESS_EVERY = 25


def _now() -> datetime:
    return datetime.now(UTC)


async def run_export_notas(session: AsyncSession, *, job_id: UUID) -> None:
    job = await session.get(BackgroundJob, job_id)
    if job is None:
        logger.error("export_notas_job_missing", job_id=str(job_id))
        return

    job.status = BackgroundJobStatus.RUNNING
    job.started_at = _now()
    job.last_heartbeat_at = _now()
    await session.commit()

    try:
        payload = job.payload or {}
        fmt = payload.get("fmt", "xlsx")
        date_from = date.fromisoformat(payload["date_from"])
        date_to = date.fromisoformat(payload["date_to"])
        conta_ids = [UUID(c) for c in payload.get("conta", [])]
        _validate_range(date_from, date_to)

        rows, erros = await _fetch_all(session, conta_ids, date_from, date_to)
        if not rows and erros:
            raise RuntimeError("falha em todas as contas: " + "; ".join(erros[:5]))
        if len(rows) > MAX_EXPORT_NOTAS_ASYNC:
            raise RuntimeError(
                f"{len(rows)} notas no período — o export é limitado a "
                f"{MAX_EXPORT_NOTAS_ASYNC} por vez, reduza o período ou as contas"
            )
        job.total = len(rows)
        job.processed = 0
        await session.commit()

        async def on_progress(done: int) -> None:
            job.processed = done
            if done % _PROGRESS_EVERY == 0:
                job.last_heartbeat_at = _now()
                await session.commit()

        if fmt == "xml":
            data, n_avisos = await build_xml_zip(rows, erros, on_progress)
            filename = _xml_zip_fname()
            media = ZIP_MEDIA
        else:
            data, n_avisos = await build_xlsx(rows, erros, on_progress)
            filename = _xlsx_fname(date_from, date_to)
            media = XLSX_MEDIA

        rel = f"notas_fiscais/{job_id}/{filename}"
        abs_path = Path(get_settings().uploads_dir) / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(data)

        job.processed = len(rows)
        job.result = {
            "file_path": rel,
            "filename": filename,
            "media_type": media,
            "fmt": fmt,
            "notas": len(rows),
            "avisos": n_avisos,
        }
        job.status = BackgroundJobStatus.SUCCEEDED
        logger.info(
            "export_notas_done",
            job_id=str(job_id),
            fmt=fmt,
            notas=len(rows),
            avisos=n_avisos,
        )
    except Exception as e:  # noqa: BLE001
        # Não re-levantamos: o arq considera o job concluído (sem retry) e o
        # BackgroundJob carrega o erro pro front mostrar.
        job.status = BackgroundJobStatus.FAILED
        job.error = f"{type(e).__name__}: {e}"[:1000]
        logger.exception("export_notas_failed", job_id=str(job_id))
    finally:
        job.finished_at = _now()
        await session.commit()
