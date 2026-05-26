"""Importação — controle de pedidos de importação (malas / China).

Schema follows the financeiro pattern: no user_id / company_id, the
data is org-wide and gated at the API by the `importacao` permission.

See migration 0088_importacao_tables for table comments.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Date,
    DateTime,
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
    # Bling sync intent — NULL = never marked, 'pending' = operator clicked
    # "Enviar pro Bling" (awaiting worker), 'sent' = created in Bling,
    # 'error' = last attempt failed. The actual Bling write integration
    # doesn't exist yet (BlingClient has no create_product); this column
    # just persists the intent so a future worker can pick it up.
    bling_sync_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    bling_sync_marked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )


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


# ── Cotação (aba independente — comparação produto × fabricante) ─────


class CotacaoFabricante(Base, TimestampMixin):
    """Bloco de coluna (3 sub-cols: capacidade/R$/USD) + 4 obs livres
    no cabeçalho. `ordem` preserva a ordem de exibição na tabela."""
    __tablename__ = "cotacao_fabricantes"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    nome: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    obs1: Mapped[str | None] = mapped_column(Text, nullable=True)
    obs2: Mapped[str | None] = mapped_column(Text, nullable=True)
    obs3: Mapped[str | None] = mapped_column(Text, nullable=True)
    obs4: Mapped[str | None] = mapped_column(Text, nullable=True)
    ordem: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CotacaoProduto(Base, TimestampMixin):
    """Linha da tabela de cotação (um produto comparado entre fabricantes)."""
    __tablename__ = "cotacao_produtos"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    nome: Mapped[str] = mapped_column(String(150), nullable=False, default="")
    ordem: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CotacaoValor(Base, TimestampMixin):
    """Célula no cruzamento produto × fabricante. Tudo digitado manualmente,
    sem fórmulas. capacidade é texto livre (operador escreve "20cm",
    "8 peças", "tamanho M", etc.)."""
    __tablename__ = "cotacao_valores"
    __table_args__ = (
        UniqueConstraint(
            "fabricante_id", "produto_id",
            name="uq_cotacao_valores_fab_prod",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    fabricante_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("cotacao_fabricantes.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    produto_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("cotacao_produtos.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    capacidade: Mapped[str | None] = mapped_column(String(50), nullable=True)
    valor_real: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    valor_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
