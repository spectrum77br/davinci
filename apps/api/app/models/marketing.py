"""Marketing module — autonomous ad agents (Shopee/ML/Amazon), per-department.

Parallel to the existing `ads_*` module: same conceptual shape but
organised by *department* (celular/mala/eletro) instead of just by account.
A single brand (Poofy, Minas, Kfa…) gets one MarketingAccount per
(platform, department) pair so the agent can target ACOS / budget per
vertical instead of treating the whole shop as one bucket.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class MarketingAccount(Base, TimestampMixin):
    __tablename__ = "marketing_accounts"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    integration_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("integrations.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # "shopee" | "ml" | "amazon"
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    # "celular" | "mala" | "eletro"
    department: Mapped[str] = mapped_column(String(32), nullable=False)

    acos_target: Mapped[float] = mapped_column(
        Float, nullable=False, default=8.0, server_default=text("8.0")
    )
    daily_budget: Mapped[float | None] = mapped_column(Float, nullable=True)
    agent_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    # "active" | "reduced" | "paused" | "off"
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default=text("'active'")
    )
    current_intensity: Mapped[int] = mapped_column(
        Integer, nullable=False, default=50, server_default=text("50")
    )
    credit_balance: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Timestamp of the last successful Shopee `get_total_balance` call.
    # The orchestrator uses this to decide whether to call the API again
    # (TTL 6h) or reuse the cached `credit_balance`. Shopee's per-endpoint
    # throttle makes naive every-tick polling impossible across 13 shops.
    credit_balance_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    spend_today: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default=text("0"))
    revenue_today: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default=text("0"))
    impressions_today: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))

    # ─── Automatic schedule (BRT windows) ────────────────────────────────
    # When True, the reconciler (on the agent node) keeps this account's
    # campaigns paused outside the MarketingSchedule windows and resumed
    # inside them — in America/Sao_Paulo time, see services/marketing/scheduling.py.
    schedule_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # Manual override on top of the schedule. 'pause' forces OFF, 'resume'
    # forces ON, NULL = follow the windows. `override_until` bounds it
    # (NULL = until the operator clears it). The reconciler honours the
    # override while it's active, then falls back to the windows.
    override_action: Mapped[str | None] = mapped_column(String(16), nullable=True)
    override_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class MarketingCommand(Base, TimestampMixin):
    """Outbox queue for ad actions. Every action — manual (a button click)
    or automatic (the schedule reconciler) — becomes one `pending` row here.
    The dedicated agent node consumes it via SELECT … FOR UPDATE SKIP LOCKED,
    runs it through `edit_campaign`, and stamps result/status. Durable across
    restarts: an interrupted command stays `pending`/`claimed` and reconverges
    on the next tick."""

    __tablename__ = "marketing_commands"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketing_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    # NULL = apply to ALL campaigns of the account (whole-account pause/resume).
    campaign_external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    # "pause" | "resume" | "set_budget" | "adjust_budget_pct"
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    # "pending" | "claimed" | "done" | "failed"
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default=text("'pending'")
    )
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    # "manual" | "schedule"
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="manual", server_default=text("'manual'")
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MarketingSchedule(Base, TimestampMixin):
    __tablename__ = "marketing_schedules"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketing_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)  # 0=Mon
    start_hour: Mapped[int] = mapped_column(Integer, nullable=False)
    end_hour: Mapped[int] = mapped_column(Integer, nullable=False)


class MarketingMetric(Base, TimestampMixin):
    __tablename__ = "marketing_metrics"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketing_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    spend: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default=text("0"))
    revenue: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default=text("0"))
    impressions: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    clicks: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    orders: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    acos: Mapped[float | None] = mapped_column(Float, nullable=True)
    intensity: Mapped[int] = mapped_column(
        Integer, nullable=False, default=50, server_default=text("50")
    )


class MarketingDecision(Base, TimestampMixin):
    __tablename__ = "marketing_decisions"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketing_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # "no_action" | "enable_all" | "disable_all" | "increase_budget" |
    # "decrease_budget" | "pause_worst"
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    market_intensity: Mapped[int] = mapped_column(Integer, nullable=False)
    in_base_window: Mapped[bool] = mapped_column(Boolean, nullable=False)
    acos_at_time: Mapped[float | None] = mapped_column(Float, nullable=True)


class MarketingCampaign(Base, TimestampMixin):
    """One ad campaign inside a MarketingAccount. Holds the same six rolled-up
    metrics that the dashboard's period tables show (Crédito, Gasto,
    Faturamento, Impressões, ACOS, Status) so the spreadsheet grid can render
    directly off this row without re-aggregating per cell. `credit` is only
    meaningful for Shopee (`credit_balance` on the parent account is the
    shop-level pot; this column lets a future real-API hook keep a per-
    campaign credit too — null for ML/Amazon)."""

    __tablename__ = "marketing_campaigns"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketing_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # "active" | "reduced" | "paused" | "off"
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default=text("'active'")
    )

    credit: Mapped[float | None] = mapped_column(Float, nullable=True)
    spend: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default=text("0"))
    revenue: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default=text("0"))
    impressions: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    acos: Mapped[float | None] = mapped_column(Float, nullable=True)


class MarketingPattern(Base, TimestampMixin):
    __tablename__ = "marketing_patterns"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketing_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    # "peak_hour" | "slow_hour" | "peak_day" | "slow_day" | "seasonal" | "correlation"
    pattern_type: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
