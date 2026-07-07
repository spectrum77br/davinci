from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, LargeBinary, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import IntegrationPlatform


def _enum(py_enum, name: str):
    return Enum(
        py_enum,
        name=name,
        schema=None,
        create_type=False,
        values_callable=lambda x: [e.value for e in x],
    )


class Integration(Base, TimestampMixin):
    __tablename__ = "integrations"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    store_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="SET NULL"),
        unique=True,
        nullable=True,
    )
    platform: Mapped[IntegrationPlatform] = mapped_column(
        _enum(IntegrationPlatform, "integration_platform"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    credentials: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'active'"))
    # "Arquivada": conta suspensa que o operador tira de circulação pela tela
    # Lojas. Quando != NULL, a integração some de Produtos e do filtro de contas,
    # e o sync (push de estoque/preço) para de mirá-la. NULL = ativa. Reversível
    # pelo botão "Ativar". Migration 0170.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Marketing module: per-integration department, optional Bling loja
    # override, and ads-enabled opt-in. The cron only pulls from
    # `ads_enabled=True` rows so unrelated Bling/Shopee integrations don't
    # eat API quota.
    department: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bling_loja_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ads_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # "Modo férias": quando True, o SyncOrchestrator NÃO empurra estoque pra
    # esta conta do marketplace (freeze — o anúncio mantém o último estoque
    # enviado). Só afeta o push de estoque; preços, pedidos, ads e OAuth
    # seguem normais. Default false = comportamento inalterado das integrações
    # existentes.
    vacation_mode: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # Counter incremented on each marketing-sync failure, reset on
    # success. Used by alerts.notify_consecutive_failures to fire
    # Telegram on exactly the 3rd consecutive miss so flaky APIs don't
    # spam the operator.
    consecutive_errors: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default=text("0")
    )
    # Stamp of the last completed marketing Ads sync for this integration.
    # Used by `sync_shopee_single_next` as a round-robin cursor so the
    # 5-min cron picks the staleest shop each tick — necessary because
    # Shopee's per-partner Ads throttle can't sustain a batch over 13 shops.
    last_ads_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class OAuthState(Base):
    __tablename__ = "oauth_states"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    state: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    platform: Mapped[IntegrationPlatform] = mapped_column(
        _enum(IntegrationPlatform, "integration_platform"), nullable=False
    )
    store_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="CASCADE"),
        nullable=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    code_verifier: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
