from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import UserRole, UserStatus


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    open_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    role: Mapped[UserRole] = mapped_column(
        Enum(
            UserRole,
            name="user_role",
            schema=None,
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=UserRole.USER,
        server_default=text("'user'"),
    )
    status: Mapped[UserStatus] = mapped_column(
        Enum(
            UserStatus,
            name="user_status",
            schema=None,
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=UserStatus.PENDING,
        server_default=text("'pending'"),
    )

    tuta: Mapped[str | None] = mapped_column(String(255), nullable=True)
    upseller: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bling_login: Mapped[str | None] = mapped_column(String(255), nullable=True)
    adspower: Mapped[str | None] = mapped_column(String(255), nullable=True)
    duoke: Mapped[str | None] = mapped_column(String(255), nullable=True)

    permissions: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Operator-of-stock tag. When non-null AND role != admin, this user
    # is locked to /controle-estoque (sees only products whose SKU ends
    # with `.{stock_tag}`). Valid values: ci / pi / ra / sa / sp.
    stock_tag: Mapped[str | None] = mapped_column(String(16), nullable=True)
