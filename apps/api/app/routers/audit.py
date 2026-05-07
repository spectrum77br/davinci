"""Audit-by-spreadsheet router (Fase 10)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import (
    AuditFinding,
    AuditFindingStatus,
    AuditRun,
    AuditRunStatus,
    AuditUpload,
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    PricingAccount,
    User,
)
from app.schemas.audit import (
    AuditFindingOut,
    AuditFindingsPage,
    AuditFixIn,
    AuditFixResult,
    AuditPreviewIn,
    AuditPreviewOut,
    AuditRunCreate,
    AuditRunOut,
    AuditUploadOut,
)
from app.schemas.products import JobCreatedOut
from app.services.audit.parser import AuditParseError, list_sheets, preview
from app.services.pricing.push import push_one
from app.services.storage import LocalStorage
from app.worker_pool import get_arq_pool

logger = structlog.get_logger()
router = APIRouter(prefix="/api/audit", tags=["audit"])

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB


def _coerce_finding_status(v: str) -> AuditFindingStatus:
    try:
        return AuditFindingStatus(v)
    except ValueError as e:
        raise HTTPException(400, detail={"code": "invalid_status"}) from e


# =============================================================================
# Uploads
# =============================================================================

@router.post(
    "/uploads",
    response_model=AuditUploadOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_xlsx(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("auditoria", "edit"))],
    file: Annotated[UploadFile, File(...)],
) -> AuditUploadOut:
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, detail={"code": "unsupported_file_type"})

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, detail={"code": "file_too_large"})

    storage = LocalStorage()
    path = await storage.save(user_id=user.id, content=content, suffix=".xlsx")

    try:
        sheets = await asyncio.to_thread(list_sheets, str(path))
    except AuditParseError as e:
        await storage.delete(path)
        raise HTTPException(400, detail={"code": "invalid_xlsx", "reason": str(e)}) from e

    row = AuditUpload(
        user_id=user.id,
        filename=file.filename or "upload.xlsx",
        file_path=str(path),
        size_bytes=len(content),
        sheets=sheets,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return AuditUploadOut.model_validate(row)


@router.get("/uploads", response_model=list[AuditUploadOut])
async def list_uploads(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("auditoria", "view"))],
) -> list[AuditUploadOut]:
    rows = (
        await session.execute(
            select(AuditUpload)
            .where(AuditUpload.user_id == user.id)
            .order_by(AuditUpload.created_at.desc())
            .limit(50)
        )
    ).scalars().all()
    return [AuditUploadOut.model_validate(r) for r in rows]


@router.delete("/uploads/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_upload(
    upload_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("auditoria", "delete"))],
) -> None:
    row = (
        await session.execute(
            select(AuditUpload).where(
                and_(AuditUpload.id == upload_id, AuditUpload.user_id == user.id)
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "upload_not_found"})
    path = row.file_path
    await session.delete(row)
    await session.commit()
    await LocalStorage().delete(path)


# =============================================================================
# Parse + preview
# =============================================================================

@router.post("/parse", response_model=AuditPreviewOut)
async def parse_sheet(
    body: AuditPreviewIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("auditoria", "view"))],
) -> AuditPreviewOut:
    upload = (
        await session.execute(
            select(AuditUpload).where(
                and_(AuditUpload.id == body.upload_id, AuditUpload.user_id == user.id)
            )
        )
    ).scalar_one_or_none()
    if upload is None:
        raise HTTPException(404, detail={"code": "upload_not_found"})

    try:
        prev = await asyncio.to_thread(
            preview, upload.file_path, body.sheet, max_rows=body.max_rows
        )
    except AuditParseError as e:
        raise HTTPException(
            400, detail={"code": "parse_failed", "reason": str(e)}
        ) from e

    accounts = (
        await session.execute(
            select(PricingAccount).where(PricingAccount.user_id == user.id)
        )
    ).scalars().all()
    by_lower_name = {(a.name or "").strip().lower(): a.id for a in accounts}
    suggested: dict[str, UUID] = {}
    for h in prev.headers:
        key = (h or "").strip().lower()
        if not key:
            continue
        acc_id = by_lower_name.get(key)
        if acc_id:
            suggested[h] = acc_id

    return AuditPreviewOut(
        sheet_name=prev.sheet_name,
        headers=prev.headers,
        sku_column=prev.sku_column,
        rows=prev.rows,
        total_rows=prev.total_rows,
        suggested_account_map=suggested,
    )


# =============================================================================
# Runs
# =============================================================================

@router.post("/runs", response_model=JobCreatedOut, status_code=status.HTTP_201_CREATED)
async def create_run(
    body: AuditRunCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("auditoria", "edit"))],
) -> JobCreatedOut:
    upload = (
        await session.execute(
            select(AuditUpload).where(
                and_(AuditUpload.id == body.upload_id, AuditUpload.user_id == user.id)
            )
        )
    ).scalar_one_or_none()
    if upload is None:
        raise HTTPException(404, detail={"code": "upload_not_found"})

    if not body.account_map:
        raise HTTPException(400, detail={"code": "empty_account_map"})

    account_ids = list(body.account_map.values())
    owned = (
        await session.execute(
            select(PricingAccount.id).where(
                and_(
                    PricingAccount.user_id == user.id,
                    PricingAccount.id.in_(account_ids),
                )
            )
        )
    ).scalars().all()
    if len(set(owned)) != len(set(account_ids)):
        raise HTTPException(400, detail={"code": "account_map_invalid"})

    job = BackgroundJob(
        type=BackgroundJobType.AUDIT,
        status=BackgroundJobStatus.PENDING,
        created_by=user.id,
        payload={"upload_id": str(upload.id), "sheet": body.sheet},
    )
    session.add(job)
    await session.flush()

    run = AuditRun(
        user_id=user.id,
        upload_id=upload.id,
        job_id=job.id,
        sheet_name=body.sheet,
        account_map={k: str(v) for k, v in body.account_map.items()},
        status=AuditRunStatus.PENDING,
    )
    session.add(run)
    await session.flush()

    pool = await get_arq_pool()
    arq = await pool.enqueue_job(
        "audit_run", str(job.id), str(run.id), str(user.id)
    )
    if arq is not None:
        job.arq_job_id = arq.job_id
    await session.commit()
    return JobCreatedOut(job_id=job.id)


@router.get("/runs", response_model=list[AuditRunOut])
async def list_runs(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("auditoria", "view"))],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[AuditRunOut]:
    rows = (
        await session.execute(
            select(AuditRun)
            .where(AuditRun.user_id == user.id)
            .order_by(AuditRun.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [AuditRunOut.model_validate(r) for r in rows]


@router.get("/runs/{run_id}", response_model=AuditRunOut)
async def get_run(
    run_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("auditoria", "view"))],
) -> AuditRunOut:
    row = (
        await session.execute(
            select(AuditRun).where(
                and_(AuditRun.id == run_id, AuditRun.user_id == user.id)
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "run_not_found"})
    return AuditRunOut.model_validate(row)


@router.get("/runs/{run_id}/findings", response_model=AuditFindingsPage)
async def list_findings(
    run_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("auditoria", "view"))],
    status_in: Annotated[list[str] | None, Query()] = None,
    sku: Annotated[str | None, Query()] = None,
    fixed: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditFindingsPage:
    owned = (
        await session.execute(
            select(AuditRun.id).where(
                and_(AuditRun.id == run_id, AuditRun.user_id == user.id)
            )
        )
    ).scalar_one_or_none()
    if owned is None:
        raise HTTPException(404, detail={"code": "run_not_found"})

    where = [AuditFinding.run_id == run_id]
    if status_in:
        statuses = [_coerce_finding_status(s) for s in status_in]
        where.append(AuditFinding.status.in_(statuses))
    if sku:
        where.append(AuditFinding.sku.ilike(f"%{sku}%"))
    if fixed is not None:
        where.append(AuditFinding.fixed == fixed)

    total = (
        await session.execute(
            select(func.count(AuditFinding.id)).where(and_(*where))
        )
    ).scalar_one()
    rows = (
        await session.execute(
            select(AuditFinding)
            .where(and_(*where))
            .order_by(AuditFinding.status, AuditFinding.sku)
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return AuditFindingsPage(
        items=[AuditFindingOut.model_validate(r) for r in rows],
        total=int(total or 0),
        limit=limit,
        offset=offset,
    )


# =============================================================================
# Fix prices
# =============================================================================

@router.post("/findings/{finding_id}/fix-price", response_model=AuditFixResult)
async def fix_one(
    finding_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("auditoria", "edit"))],
) -> AuditFixResult:
    f = (
        await session.execute(
            select(AuditFinding).where(
                and_(AuditFinding.id == finding_id, AuditFinding.user_id == user.id)
            )
        )
    ).scalar_one_or_none()
    if f is None:
        raise HTTPException(404, detail={"code": "finding_not_found"})
    return await _fix_findings(session, user=user, findings=[f])


@router.post("/runs/{run_id}/fix-prices", response_model=AuditFixResult)
async def fix_bulk(
    run_id: UUID,
    body: AuditFixIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("auditoria", "edit"))],
) -> AuditFixResult:
    run_owned = (
        await session.execute(
            select(AuditRun.id).where(
                and_(AuditRun.id == run_id, AuditRun.user_id == user.id)
            )
        )
    ).scalar_one_or_none()
    if run_owned is None:
        raise HTTPException(404, detail={"code": "run_not_found"})

    where = [AuditFinding.run_id == run_id, AuditFinding.user_id == user.id]
    if body.finding_ids:
        where.append(AuditFinding.id.in_(body.finding_ids))
    if body.status_in:
        statuses = [_coerce_finding_status(s) for s in body.status_in]
        where.append(AuditFinding.status.in_(statuses))
    if not body.finding_ids and not body.status_in:
        # Default: only price_mismatch
        where.append(AuditFinding.status == AuditFindingStatus.PRICE_MISMATCH)

    findings = (
        await session.execute(select(AuditFinding).where(and_(*where)))
    ).scalars().all()
    return await _fix_findings(session, user=user, findings=findings)


async def _fix_findings(
    session: AsyncSession,
    *,
    user: User,
    findings: list[AuditFinding],
) -> AuditFixResult:
    fixed = 0
    failed = 0
    skipped = 0
    details: list[dict] = []
    now = datetime.now(UTC)

    for f in findings:
        if f.fixed:
            skipped += 1
            details.append({"id": str(f.id), "code": "already_fixed"})
            continue
        if f.pricing_account_id is None or f.pricing_product_id is None:
            skipped += 1
            details.append({"id": str(f.id), "code": "no_target"})
            continue

        outcome = await push_one(
            session,
            user=user,
            account_id=f.pricing_account_id,
            product_id=f.pricing_product_id,
            idempotency_key=f"audit:{f.id}",
        )
        if outcome.ok:
            f.fixed = True
            f.fixed_at = now
            fixed += 1
        else:
            failed += 1
        details.append(
            {
                "id": str(f.id),
                "ok": outcome.ok,
                "code": outcome.code,
                "detail": outcome.detail,
            }
        )

    await session.commit()
    return AuditFixResult(
        fixed=fixed, failed=failed, skipped=skipped, details=details
    )


@router.delete("/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_run(
    run_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("auditoria", "delete"))],
) -> None:
    res = await session.execute(
        delete(AuditRun).where(
            and_(AuditRun.id == run_id, AuditRun.user_id == user.id)
        )
    )
    if res.rowcount == 0:
        raise HTTPException(404, detail={"code": "run_not_found"})
    await session.commit()
