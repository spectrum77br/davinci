"""Dashboard router (Fase 12).

Aggregates KPIs and lightweight series for the home page:
  - product / integration / listing / alert counts
  - listings by platform (chart)
  - recent background_jobs (sync activity)
  - onboarding step status (mirrors `/onboarding`)
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_active_user, user_scope
from app.models import (
    Alert,
    BackgroundJob,
    Company,
    Integration,
    IntegrationPlatform,
    Listing,
    Product,
    ProductLink,
    User,
)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


class KPI(BaseModel):
    products_total: int
    products_active: int
    integrations_total: int
    integrations_connected: int
    listings_total: int
    listings_linked: int
    alerts_unread: int


class ChannelShare(BaseModel):
    platform: str
    listings: int
    linked: int


class RecentSync(BaseModel):
    id: UUID
    type: str
    status: str
    total: int
    processed: int
    started_at: datetime | None
    finished_at: datetime | None


class OnboardingStep(BaseModel):
    key: str
    done: bool


class DashboardOut(BaseModel):
    kpis: KPI
    channels: list[ChannelShare]
    recent_syncs: list[RecentSync]
    onboarding: list[OnboardingStep]
    needs_onboarding: bool


@router.get("", response_model=DashboardOut)
async def get_dashboard(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
) -> DashboardOut:
    products_total = (
        await session.execute(
            select(func.count()).select_from(Product).where(user_scope(Product, user))
        )
    ).scalar_one()
    products_active = (
        await session.execute(
            select(func.count())
            .select_from(Product)
            .where(user_scope(Product, user), Product.stock > 0)
        )
    ).scalar_one()

    integrations_total = (
        await session.execute(
            select(func.count())
            .select_from(Integration)
            .where(user_scope(Integration, user))
        )
    ).scalar_one()
    integrations_connected = (
        await session.execute(
            select(func.count())
            .select_from(Integration)
            .where(
                user_scope(Integration, user),
                Integration.last_test_ok.is_(True),
            )
        )
    ).scalar_one()

    listings_total = (
        await session.execute(
            select(func.count()).select_from(Listing).where(user_scope(Listing, user))
        )
    ).scalar_one()
    listings_linked = (
        await session.execute(
            select(func.count())
            .select_from(Listing)
            .where(user_scope(Listing, user), Listing.product_id.is_not(None))
        )
    ).scalar_one()

    alerts_unread = (
        await session.execute(
            select(func.count())
            .select_from(Alert)
            .where(user_scope(Alert, user), Alert.read_at.is_(None))
        )
    ).scalar_one()

    listing_rows = (
        await session.execute(
            select(
                Listing.platform,
                func.count().label("listings"),
                func.count(Listing.product_id).label("linked"),
            )
            .where(user_scope(Listing, user))
            .group_by(Listing.platform)
        )
    ).all()
    channels = [
        ChannelShare(
            platform=(r.platform.value if hasattr(r.platform, "value") else str(r.platform)),
            listings=int(r.listings or 0),
            linked=int(r.linked or 0),
        )
        for r in listing_rows
    ]

    job_rows = (
        await session.execute(
            select(BackgroundJob)
            .where(BackgroundJob.created_by == user.id)
            .order_by(BackgroundJob.created_at.desc())
            .limit(5)
        )
    ).scalars().all()
    recent_syncs = [
        RecentSync(
            id=j.id,
            type=j.type.value if hasattr(j.type, "value") else str(j.type),
            status=j.status.value if hasattr(j.status, "value") else str(j.status),
            total=j.total or 0,
            processed=j.processed or 0,
            started_at=j.started_at,
            finished_at=j.finished_at,
        )
        for j in job_rows
    ]

    has_company = (
        await session.execute(select(func.count()).select_from(Company))
    ).scalar_one() > 0
    has_bling = (
        await session.execute(
            select(func.count())
            .select_from(Integration)
            .where(
                user_scope(Integration, user),
                Integration.platform == IntegrationPlatform.BLING,
            )
        )
    ).scalar_one() > 0
    has_other_integration = integrations_total - (1 if has_bling else 0) > 0
    has_links = (
        await session.execute(
            select(func.count())
            .select_from(ProductLink)
            .where(user_scope(ProductLink, user))
        )
    ).scalar_one() > 0
    has_products = products_total > 0
    has_listings = listings_total > 0

    onboarding = [
        OnboardingStep(key="company", done=has_company),
        OnboardingStep(key="bling", done=has_bling),
        OnboardingStep(key="products", done=has_products),
        OnboardingStep(key="marketplaces", done=has_other_integration),
        OnboardingStep(key="links", done=has_listings and has_links),
    ]
    needs_onboarding = integrations_total == 0

    return DashboardOut(
        kpis=KPI(
            products_total=products_total,
            products_active=products_active,
            integrations_total=integrations_total,
            integrations_connected=integrations_connected,
            listings_total=listings_total,
            listings_linked=listings_linked,
            alerts_unread=alerts_unread,
        ),
        channels=channels,
        recent_syncs=recent_syncs,
        onboarding=onboarding,
        needs_onboarding=needs_onboarding,
    )
