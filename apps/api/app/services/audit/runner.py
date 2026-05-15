"""AuditRunner (Fase 10).

For each (sku, account_column) cell in the planilha:
  - resolve `pricing_product` by `(user_id, sku)` → if missing → status=missing.
  - resolve `pricing_account` from `account_map[column_header]`.
  - compute `expected_price = calculate(account, product, override)`.
  - planilha cell `actual_price`.
  - classify:
      missing  — no pricing_product
      paused   — listing exists but status != 'active' (or no listing on
                 the account's integration)
      ok       — |expected - actual| ≤ 0.01 (one cent tolerance)
      price_mismatch — otherwise
  - skip cells where `actual_price` is None (empty cell ⇒ no comparison).

Persists one `audit_findings` row per non-empty (sku, account) cell.
Heartbeats `audit_runs.processed` and `background_jobs.last_heartbeat_at`
every 25 SKUs so the UI can show progress.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import structlog
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AuditFinding,
    AuditFindingStatus,
    AuditRun,
    AuditRunStatus,
    AuditUpload,
    BackgroundJob,
    BackgroundJobStatus,
    Listing,
    ListingStatus,
    PricingAccount,
    PricingOverride,
    PricingProduct,
)
from app.services.audit.parser import AuditParseError, iter_rows, to_decimal
from app.services.pricing.calc import calculate

logger = structlog.get_logger()

PRICE_TOL = Decimal("0.01")


def _now() -> datetime:
    return datetime.now(UTC)


async def _classify_listing_status(
    session: AsyncSession,
    *,
    integration_id: UUID | None,
    sku: str,
) -> tuple[bool, str | None]:
    """Returns (is_paused, detail). Paused if the listing exists but isn't
    active. If no listing exists for that account, also treated as paused
    (the seller hasn't published it on this account)."""
    if integration_id is None:
        return True, "account_not_linked"
    row = (
        await session.execute(
            select(Listing.status).where(
                and_(
                    Listing.integration_id == integration_id,
                    Listing.sku == sku,
                )
            ).limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return True, "no_listing"
    if isinstance(row, ListingStatus):
        active = row == ListingStatus.ACTIVE
    else:
        active = str(row) == "active"
    return (not active, None if active else f"listing_status={row}")


async def run_audit(
    session: AsyncSession,
    *,
    job_id: UUID,
    run_id: UUID,
    user_id: UUID,
) -> None:
    job = await session.get(BackgroundJob, job_id)
    run = await session.get(AuditRun, run_id)
    if job is None or run is None:
        logger.error("audit_missing", job_id=str(job_id), run_id=str(run_id))
        return

    job.status = BackgroundJobStatus.RUNNING
    job.started_at = _now()
    job.last_heartbeat_at = _now()
    run.status = AuditRunStatus.RUNNING
    run.started_at = _now()
    await session.commit()

    upload = await session.get(AuditUpload, run.upload_id)
    if upload is None:
        await _fail(session, run=run, job=job, error="upload_missing")
        return

    # Pre-load all products + accounts + overrides into in-memory dicts.
    products = (
        await session.execute(select(PricingProduct))
    ).scalars().all()
    by_sku = {p.sku: p for p in products}

    accounts = (
        await session.execute(select(PricingAccount))
    ).scalars().all()
    accounts_by_id = {a.id: a for a in accounts}

    overrides = (
        await session.execute(select(PricingOverride))
    ).scalars().all()
    by_pair: dict[tuple[UUID, UUID], PricingOverride] = {
        (o.pricing_product_id, o.pricing_account_id): o for o in overrides
    }

    # Resolve account_map (col_header → uuid) into account objects.
    raw_map: dict[str, str] = run.account_map or {}
    column_to_account: dict[str, PricingAccount] = {}
    for header, acc_id in raw_map.items():
        try:
            uid = UUID(str(acc_id))
        except ValueError:
            continue
        acc = accounts_by_id.get(uid)
        if acc is not None:
            column_to_account[header] = acc

    summary = {
        "total_cells": 0,
        "ok": 0,
        "price_mismatch": 0,
        "missing": 0,
        "paused": 0,
        "skipped_empty": 0,
        "rows": 0,
    }

    try:
        gen = iter_rows(upload.file_path, run.sheet_name)
        header_row = next(gen, None)
        if header_row is None:
            await _fail(session, run=run, job=job, error="empty_sheet")
            return
        _, headers = header_row

        sku_idx: int | None = None
        for i, h in enumerate(headers):
            if (h or "").strip().lower() in {"sku", "código", "codigo", "cod"}:
                sku_idx = i
                break
        if sku_idx is None:
            await _fail(session, run=run, job=job, error="sku_column_not_found")
            return

        column_indexes: list[tuple[int, str, PricingAccount]] = []
        for i, h in enumerate(headers):
            acc = column_to_account.get(h)
            if acc is not None:
                column_indexes.append((i, h, acc))

        if not column_indexes:
            await _fail(session, run=run, job=job, error="no_account_columns_mapped")
            return

        new_findings: list[AuditFinding] = []
        seen_skus = 0
        for _n, raw_row in gen:
            if sku_idx >= len(raw_row):
                continue
            sku_cell = raw_row[sku_idx]
            sku = (str(sku_cell).strip() if sku_cell is not None else "")
            if not sku:
                continue
            seen_skus += 1
            summary["rows"] = seen_skus

            product = by_sku.get(sku)

            for col_i, col_header, account in column_indexes:
                actual = (
                    to_decimal(raw_row[col_i]) if col_i < len(raw_row) else None
                )
                if actual is None:
                    summary["skipped_empty"] += 1
                    continue
                summary["total_cells"] += 1

                if product is None:
                    f = AuditFinding(
                        run_id=run.id,
                        user_id=user_id,
                        sku=sku,
                        pricing_product_id=None,
                        pricing_account_id=account.id,
                        column_header=col_header,
                        expected_price=None,
                        actual_price=actual,
                        status=AuditFindingStatus.MISSING,
                        detail="sku not in pricing_products",
                    )
                    new_findings.append(f)
                    summary["missing"] += 1
                    continue

                paused, paused_detail = await _classify_listing_status(
                    session,
                    integration_id=account.integration_id,
                    sku=sku,
                )
                if paused:
                    f = AuditFinding(
                        run_id=run.id,
                        user_id=user_id,
                        sku=sku,
                        pricing_product_id=product.id,
                        pricing_account_id=account.id,
                        column_header=col_header,
                        expected_price=None,
                        actual_price=actual,
                        status=AuditFindingStatus.PAUSED,
                        detail=paused_detail,
                    )
                    new_findings.append(f)
                    summary["paused"] += 1
                    continue

                ovr = by_pair.get((product.id, account.id))
                outcome = calculate(account, product, ovr)
                expected = outcome.price

                if expected is None:
                    f = AuditFinding(
                        run_id=run.id,
                        user_id=user_id,
                        sku=sku,
                        pricing_product_id=product.id,
                        pricing_account_id=account.id,
                        column_header=col_header,
                        expected_price=None,
                        actual_price=actual,
                        status=AuditFindingStatus.PRICE_MISMATCH,
                        detail=f"expected_unavailable: {outcome.source}",
                    )
                    new_findings.append(f)
                    summary["price_mismatch"] += 1
                    continue

                diff = abs(expected - actual)
                if diff <= PRICE_TOL:
                    status = AuditFindingStatus.OK
                    summary["ok"] += 1
                    detail = None
                else:
                    status = AuditFindingStatus.PRICE_MISMATCH
                    summary["price_mismatch"] += 1
                    detail = f"diff={diff}"

                new_findings.append(
                    AuditFinding(
                        run_id=run.id,
                        user_id=user_id,
                        sku=sku,
                        pricing_product_id=product.id,
                        pricing_account_id=account.id,
                        column_header=col_header,
                        expected_price=expected,
                        actual_price=actual,
                        status=status,
                        detail=detail,
                    )
                )

            run.processed = seen_skus
            if seen_skus % 25 == 0:
                session.add_all(new_findings)
                new_findings = []
                run.summary = summary
                job.processed = seen_skus
                job.last_heartbeat_at = _now()
                await session.commit()

        if new_findings:
            session.add_all(new_findings)
        run.total = seen_skus
        run.processed = seen_skus
        run.summary = summary
        run.status = AuditRunStatus.SUCCEEDED
        run.finished_at = _now()
        job.total = seen_skus
        job.processed = seen_skus
        job.status = BackgroundJobStatus.SUCCEEDED
        job.result = {"summary": summary, "run_id": str(run.id)}
        job.finished_at = _now()
        await session.commit()
        logger.info(
            "audit_run_finished",
            run_id=str(run.id),
            user_id=str(user_id),
            **summary,
        )
    except AuditParseError as e:
        await _fail(session, run=run, job=job, error=f"parse_error:{e}")
    except Exception as e:  # noqa: BLE001
        logger.exception("audit_run_crashed", run_id=str(run.id))
        await _fail(session, run=run, job=job, error=str(e))


async def _fail(
    session: AsyncSession, *, run: AuditRun, job: BackgroundJob, error: str
) -> None:
    run.status = AuditRunStatus.FAILED
    run.error = error
    run.finished_at = _now()
    job.status = BackgroundJobStatus.FAILED
    job.error = error
    job.finished_at = _now()
    await session.commit()
