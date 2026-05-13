from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import (
    CadastroStatus,
    CadastroTipo,
    Marketplace,
    StoreStatus,
)


def _enum(py_enum, name: str):
    return Enum(
        py_enum,
        name=name,
        schema=None,
        create_type=False,
        values_callable=lambda x: [e.value for e in x],
    )


class Company(Base, TimestampMixin):
    __tablename__ = "companies"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    razao_social: Mapped[str] = mapped_column(Text, nullable=False)
    apelido: Mapped[str] = mapped_column(Text, nullable=False)
    responsavel_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    uf: Mapped[str | None] = mapped_column(String(2), nullable=True)
    cnpj: Mapped[str | None] = mapped_column(String(14), unique=True, nullable=True)
    inscricao_estadual: Mapped[str | None] = mapped_column(Text, nullable=True)
    site_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    obs: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled_marketplaces: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        server_default=text(
            "ARRAY['ml','shopee','amazon','aliexpress','temu','tiktok','shein','magalu','site']::text[]"
        ),
        default=lambda: [
            "ml", "shopee", "amazon", "aliexpress",
            "temu", "tiktok", "shein", "magalu", "site",
        ],
    )


class Store(Base, TimestampMixin):
    __tablename__ = "stores"
    __table_args__ = (
        UniqueConstraint("company_id", "marketplace", name="uq_stores_company_id_marketplace"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    marketplace: Mapped[Marketplace] = mapped_column(_enum(Marketplace, "marketplace"), nullable=False)
    apelido_override: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[StoreStatus] = mapped_column(
        _enum(StoreStatus, "store_status"),
        nullable=False,
        default=StoreStatus.PENDING,
        server_default=text("'pending'"),
    )
    integration_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), unique=True, nullable=True)
    bling_store_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class Cadastro(Base, TimestampMixin):
    __tablename__ = "cadastros"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tipo: Mapped[CadastroTipo] = mapped_column(_enum(CadastroTipo, "cadastro_tipo"), nullable=False)
    provedor: Mapped[str | None] = mapped_column(Text, nullable=True)
    responsavel_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    codigo: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[CadastroStatus] = mapped_column(
        _enum(CadastroStatus, "cadastro_status"),
        nullable=False,
        default=CadastroStatus.ACTIVE,
        server_default=text("'active'"),
    )
    obs: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_links: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        default=dict,
    )


class CadastroStore(Base):
    __tablename__ = "cadastros_stores"

    cadastro_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("cadastros.id", ondelete="CASCADE"),
        primary_key=True,
    )
    store_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="CASCADE"),
        primary_key=True,
    )
    alias: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
