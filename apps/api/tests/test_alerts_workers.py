"""Worker tests: low_stock_polling + alerts_cleanup (Fase 6)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Alert,
    AlertSeverity,
    AlertType,
    Product,
    User,
    UserRole,
    UserStatus,
)
from app.services.alerts import emit_alert
from app.worker import alerts_cleanup, low_stock_polling


@pytest_asyncio.fixture
async def user(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:lp-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"lp-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions={},
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest.mark.asyncio
async def test_low_stock_polling_emits_alert_below_threshold(
    db: AsyncSession, user: User
) -> None:
    db.add(
        Product(
            user_id=user.id, sku="SKU-LOW", name="low",
            stock=2, min_stock=10,
        )
    )
    db.add(
        Product(
            user_id=user.id, sku="SKU-OK", name="ok",
            stock=50, min_stock=10,
        )
    )
    db.add(
        Product(
            user_id=user.id, sku="SKU-NOMIN", name="no-min",
            stock=0, min_stock=0,
        )
    )
    await db.commit()

    await low_stock_polling({})

    rows = (
        await db.execute(select(Alert).where(Alert.user_id == user.id))
    ).scalars().all()
    titles = [a.title for a in rows]
    assert any("SKU-LOW" in t for t in titles)
    assert all("SKU-OK" not in t for t in titles)
    assert all("SKU-NOMIN" not in t for t in titles)


@pytest.mark.asyncio
async def test_low_stock_polling_dedupes_same_day(
    db: AsyncSession, user: User
) -> None:
    db.add(
        Product(
            user_id=user.id, sku="DUP", name="dup",
            stock=1, min_stock=5,
        )
    )
    await db.commit()

    await low_stock_polling({})
    await low_stock_polling({})

    n = (
        await db.execute(
            select(func.count())
            .select_from(Alert)
            .where(Alert.user_id == user.id)
        )
    ).scalar_one()
    assert n == 1


@pytest.mark.asyncio
async def test_alerts_cleanup_removes_only_old(
    db: AsyncSession, user: User
) -> None:
    fresh = Alert(
        user_id=user.id,
        type=AlertType.LOW_STOCK,
        severity=AlertSeverity.WARNING,
        title="fresh",
        created_at=datetime.now(UTC) - timedelta(days=5),
    )
    stale = Alert(
        user_id=user.id,
        type=AlertType.LOW_STOCK,
        severity=AlertSeverity.WARNING,
        title="stale",
        created_at=datetime.now(UTC) - timedelta(days=61),
    )
    db.add_all([fresh, stale])
    await db.commit()

    await alerts_cleanup({})

    rows = (
        await db.execute(select(Alert).where(Alert.user_id == user.id))
    ).scalars().all()
    titles = [a.title for a in rows]
    assert "fresh" in titles
    assert "stale" not in titles


@pytest.mark.asyncio
async def test_emit_alert_dedupe_returns_none_on_conflict(
    db: AsyncSession, user: User
) -> None:
    a = await emit_alert(
        db,
        user_id=user.id,
        type=AlertType.GENERIC,
        title="hi",
        dedupe_key="k1",
    )
    await db.commit()
    assert a is not None

    a2 = await emit_alert(
        db,
        user_id=user.id,
        type=AlertType.GENERIC,
        title="dup",
        dedupe_key="k1",
    )
    await db.commit()
    assert a2 is None
