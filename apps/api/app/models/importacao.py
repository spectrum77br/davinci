"""Importação — controle de pedidos de importação (malas / China).

Schema follows the financeiro pattern: no user_id / company_id, the
data is org-wide and gated at the API by the `importacao` permission.

See migration 0088_importacao_tables for table comments.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ImportConfig(Base, TimestampMixin):
    """Singleton (id=1) com os parâmetros da fórmula de reposição."""
    __tablename__ = "import_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    tempo_reposicao: Mapped[int] = mapped_column(Integer, nullable=False, default=150)
    tempo_estoque: Mapped[int] = mapped_column(Integer, nullable=False, default=60)


class ImportProduct(Base, TimestampMixin):
    __tablename__ = "import_products"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    fornecedor: Mapped[str | None] = mapped_column(String(100), nullable=True)
    modelo_china: Mapped[str | None] = mapped_column(String(100), nullable=True)
    cor_china: Mapped[str | None] = mapped_column(String(50), nullable=True)
    fechamento: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # TSA = count of TSA locks on the suitcase (1, 2, 3) or NULL for none.
    # v1 stored this as a boolean — migration 0090 widened to integer
    # because the operator distinguishes between "no TSA" and "1 cadeado".
    tsa: Mapped[int | None] = mapped_column(Integer, nullable=True)
    modelo_bling: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sku: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    cor: Mapped[str | None] = mapped_column(String(50), nullable=True)
    custo_bling: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("0"))
    # Manual-fill fields — Bling sync is a future epic.
    estoque_bling: Mapped[int | None] = mapped_column(Integer, nullable=True)
    consumo_diario: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    maior_media_30d: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    obs: Mapped[str | None] = mapped_column(Text, nullable=True)


class ImportLote(Base, TimestampMixin):
    __tablename__ = "import_lotes"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    nome: Mapped[str] = mapped_column(String(50), nullable=False)
    abertura: Mapped[date] = mapped_column(Date, nullable=False)
    fechamento: Mapped[date | None] = mapped_column(Date, nullable=True)
    realizado: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))


class ImportLoteItem(Base, TimestampMixin):
    __tablename__ = "import_lote_items"
    __table_args__ = (
        UniqueConstraint("lote_id", "product_id", name="uq_import_lote_items_lote_product"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    lote_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("import_lotes.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("import_products.id", ondelete="CASCADE"),
        nullable=False,
    )
    quantidade: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ImportResumo(Base):
    """Lançamentos financeiros: auto-criados ao fechar um lote +
    inserções manuais (devoluções, ajustes)."""
    __tablename__ = "import_resumo"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    data: Mapped[date] = mapped_column(Date, nullable=False)
    lote_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("import_lotes.id", ondelete="SET NULL"),
        nullable=True,
    )
    lote_nome: Mapped[str | None] = mapped_column(String(50), nullable=True)
    saldo: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    obs: Mapped[str | None] = mapped_column(Text, nullable=True)
