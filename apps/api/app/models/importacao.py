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
    BigInteger,
    Boolean,
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
    categoria: Mapped[str] = mapped_column(
        String(20), nullable=False, default="mala",
        server_default=text("'mala'"), index=True,
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
    # Bling sync state — NULL = never marked, 'pending' = operator
    # clicked "Enviar pro Bling" (worker enfileirado), 'sent' = produto
    # simples criado no Bling com sucesso, 'error' = última tentativa
    # falhou. Worker em `app.services.import_product_bling_create`.
    bling_sync_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    bling_sync_marked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    # Fase real (migration 0102): rastreio completo do ciclo.
    bling_product_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    bling_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    bling_sync_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    bling_sync_done_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    # ── Cotação (aba Cotação do Celular, migration 0119) ───────────
    # valor_usd: custo unitário em USD (operador preenche).
    # valor_brl_realizado: valor R$ pago em pedidos anteriores
    #   (operador preenche, serve de comparação com previsto).
    # frete_type: 'regular' | 'swap' | 'acessorios' — define
    #   qual frete_pct usar na fórmula do previsto. Default 'regular'.
    # `valor_brl_previsto` NÃO é coluna — é calculado em tempo real no
    # frontend a partir desses + ImportCotacaoParams.
    valor_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    valor_brl_realizado: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    frete_type: Mapped[str | None] = mapped_column(String(20), nullable=True, default="regular")


class ImportCotacaoParams(Base, TimestampMixin):
    """Parâmetros globais da aba Cotação por categoria. Seed inicial só
    pra celular (migration 0119) com defaults validados pelo operador;
    mala/eletro podem ter sua linha futura."""
    __tablename__ = "import_cotacao_params"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    categoria: Mapped[str] = mapped_column(
        String(20), nullable=False, unique=True, index=True,
    )
    taxa_cambio: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False, default=Decimal("5.10"),
    )
    frete_regular_pct: Mapped[Decimal] = mapped_column(
        Numeric(6, 4), nullable=False, default=Decimal("0.16"),
    )
    frete_swap_pct: Mapped[Decimal] = mapped_column(
        Numeric(6, 4), nullable=False, default=Decimal("0.06"),
    )
    frete_acessorios_pct: Mapped[Decimal] = mapped_column(
        Numeric(6, 4), nullable=False, default=Decimal("0.20"),
    )
    adicional: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("12.00"),
    )


class ImportLote(Base, TimestampMixin):
    __tablename__ = "import_lotes"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    categoria: Mapped[str] = mapped_column(
        String(20), nullable=False, default="mala",
        server_default=text("'mala'"), index=True,
    )
    nome: Mapped[str] = mapped_column(String(50), nullable=False)
    abertura: Mapped[date] = mapped_column(Date, nullable=False)
    fechamento: Mapped[date | None] = mapped_column(Date, nullable=True)
    realizado: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    # Aba Frete do Celular (migration 0120). Não exigidos pra mala — ficam
    # NULL e a aba Frete da mala (se existir no futuro) decide se usa.
    transportadora: Mapped[str | None] = mapped_column(String(100), nullable=True)
    obs: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Override do `previsto` computed (migration 0121). Quando NULL,
    # _enrich_lote calcula via SUM(qty × custo_bling) — Mala usa assim.
    # Quando setado, sobrescreve — Celular permite operador editar
    # diretamente no header de lote ativo.
    previsto_manual: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)


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
    # Aba Frete do Celular (migration 0120). FALSE por default. Não toca
    # em items existentes (TRUE só após operador marcar manualmente).
    pago: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("FALSE"),
    )


class ImportResumo(Base):
    """Lançamentos financeiros: auto-criados ao fechar um lote +
    inserções manuais (devoluções, ajustes)."""
    __tablename__ = "import_resumo"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    categoria: Mapped[str] = mapped_column(
        String(20), nullable=False, default="mala",
        server_default=text("'mala'"), index=True,
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
    # Setado em "ajuste manual de frete" (aba Frete > botão Ajuste). Quando
    # NÃO null, a linha aparece na agregação da aba Frete; quando null,
    # é só um lançamento avulso normal (aba Resumo, etapas anteriores).
    transportadora: Mapped[str | None] = mapped_column(String(100), nullable=True)


# ── Cotação (aba independente — comparação produto × fabricante) ─────


class CotacaoFabricante(Base, TimestampMixin):
    """Bloco de coluna (3 sub-cols: capacidade/R$/USD) + 4 obs livres
    no cabeçalho. `ordem` preserva a ordem de exibição na tabela."""
    __tablename__ = "cotacao_fabricantes"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    categoria: Mapped[str] = mapped_column(
        String(20), nullable=False, default="mala",
        server_default=text("'mala'"), index=True,
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
    categoria: Mapped[str] = mapped_column(
        String(20), nullable=False, default="mala",
        server_default=text("'mala'"), index=True,
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


# ── Kit (aba Kit — matriz produto × variação) ────────────────────────


class ImportKitVariation(Base, TimestampMixin):
    """Coluna da matriz: uma das 22 variações de kit (8, 12+18,
    8+12+20+24+a075+bp003+a076, etc). Seed fixo via migration 0099.
    `code` não é UNIQUE — Excel tem duplicata legítima nas posições
    19/20 (operador anotou "corrigir, separar por cor de mochila")."""
    __tablename__ = "import_kit_variations"
    # UNIQUE composta por categoria — cada categoria (mala/celular)
    # tem seu próprio range de ordem (mala 1-22, celular 1-4).
    # Migration 0117 fez essa transição (antes era UNIQUE(ordem) global).
    # Não há UNIQUE em (categoria, code) global porque mala tem code
    # duplicado legítimo (pos 19/20). A migration 0117 cria PARTIAL
    # UNIQUE só pra celular via raw SQL (CREATE UNIQUE INDEX ... WHERE
    # categoria = 'celular') — não modelável em SQLAlchemy
    # UniqueConstraint, fica fora do __table_args__.
    __table_args__ = (
        UniqueConstraint("categoria", "ordem", name="uq_import_kit_variations_cat_ordem"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    categoria: Mapped[str] = mapped_column(
        String(20), nullable=False, default="mala",
        server_default=text("'mala'"), index=True,
    )
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    ordem: Mapped[int] = mapped_column(Integer, nullable=False)
    highlight: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    obs: Mapped[str | None] = mapped_column(Text, nullable=True)


class ImportKitBase(Base, TimestampMixin):
    """Linha da matriz: família-produto (M2 lisa b001 branca, P5 seta
    b099, mochila bp001 bege, etc). Seed fixo via migration 0099."""
    __tablename__ = "import_kit_bases"
    __table_args__ = (
        UniqueConstraint("sku_base", name="uq_import_kit_bases_sku_base"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    categoria: Mapped[str] = mapped_column(
        String(20), nullable=False, default="mala",
        server_default=text("'mala'"), index=True,
    )
    modelo_bling: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sku_base: Mapped[str] = mapped_column(String(50), nullable=False)
    cor: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ordem: Mapped[int] = mapped_column(Integer, nullable=False)


class ImportKitMark(Base):
    """Célula: existe sse "x" marcado no cruzamento base × variação.
    Sem TimestampMixin — toggle é alto-volume, só created_at importa
    pra audit (deletes não deixam histórico)."""
    __tablename__ = "import_kit_marks"
    __table_args__ = (
        UniqueConstraint(
            "base_id", "variation_id", name="uq_import_kit_marks_base_var",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    categoria: Mapped[str] = mapped_column(
        String(20), nullable=False, default="mala",
        server_default=text("'mala'"), index=True,
    )
    base_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("import_kit_bases.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    variation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("import_kit_variations.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=text("now()"),
    )
    # Estado de sincronização com o Bling. Migration 0100. O worker
    # `create_bling_kit_for_mark` é idempotente — pula se
    # bling_product_id já está preenchido.
    bling_product_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    bling_sync_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    bling_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    bling_sync_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    bling_sync_done_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    # Fase 3 (migration 0101): após o Bling create dar 'sent', criar/
    # atualizar pricing_product em /pricing/tabela. Agrupa cores do
    # mesmo modelo×variation em uma só row (sku comma-separated).
    pricing_product_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("pricing_products.id", ondelete="SET NULL"),
        nullable=True,
    )
    pricing_sync_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    pricing_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    pricing_sync_done_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
