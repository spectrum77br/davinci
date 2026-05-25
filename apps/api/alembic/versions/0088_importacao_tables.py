# ruff: noqa: E501
"""Importação — controle de pedidos de importação de malas (China).

Cinco tabelas:
  * import_config       — singleton (id=1) com tempo_reposicao (150) +
                          tempo_estoque (60). São os parâmetros da
                          fórmula de reposição.
  * import_products     — SKUs sob acompanhamento. Estoque/consumo/
                          média_30d são colunas nullable preenchidas
                          manualmente nesta v1 (TODO: sync Bling).
  * import_lotes        — Pedidos abertos com o fornecedor. Quando
                          `fechamento` é preenchido, o router insere
                          uma linha em import_resumo automaticamente.
  * import_lote_items   — Matriz N:M lote × produto carregando a
                          quantidade pedida desse SKU naquele lote.
  * import_resumo       — Lançamentos financeiros: cada fechamento de
                          lote vira uma linha aqui; o operador também
                          pode incluir ajustes manuais (devoluções,
                          etc.).

Seed:
  * import_config(id=1) com defaults.
  * 15 lançamentos em import_resumo migrados da planilha original
    (lotes 11..24 + 1 ajuste manual de devolução).

Idempotente — só seeda se as tabelas estão vazias.

Revision ID: 0088_importacao_tables
Revises: 0087_pricing_overrides_cell_color
Create Date: 2026-05-25
"""

from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0088_importacao_tables"
down_revision: str | None = "0087_pricing_overrides_cell_color"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    # ── import_config (singleton id=1) ───────────────────────────────
    op.create_table(
        "import_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tempo_reposicao", sa.Integer(), nullable=False, server_default="150"),
        sa.Column("tempo_estoque", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )
    op.execute(f"INSERT INTO {SCHEMA}.import_config (id) VALUES (1) ON CONFLICT (id) DO NOTHING")

    # ── import_products ──────────────────────────────────────────────
    op.create_table(
        "import_products",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("fornecedor", sa.String(100), nullable=True),
        sa.Column("modelo_china", sa.String(100), nullable=True),
        sa.Column("cor_china", sa.String(50), nullable=True),
        sa.Column("fechamento", sa.String(50), nullable=True),  # tipo de fechamento (ziper/tsa)
        sa.Column("tsa", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("modelo_bling", sa.String(100), nullable=True),
        sa.Column("sku", sa.String(50), nullable=False),
        sa.Column("cor", sa.String(50), nullable=True),
        sa.Column("custo_bling", sa.Numeric(10, 2), nullable=False, server_default="0"),
        # Manual fallback while Bling sync isn't wired:
        sa.Column("estoque_bling", sa.Integer(), nullable=True),
        sa.Column("consumo_diario", sa.Numeric(10, 4), nullable=True),
        sa.Column("maior_media_30d", sa.Numeric(10, 4), nullable=True),
        sa.Column("obs", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_import_products_sku", "import_products", ["sku"], schema=SCHEMA)

    # ── import_lotes ─────────────────────────────────────────────────
    op.create_table(
        "import_lotes",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("nome", sa.String(50), nullable=False),
        sa.Column("abertura", sa.Date(), nullable=False),
        sa.Column("fechamento", sa.Date(), nullable=True),
        sa.Column("realizado", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )

    # ── import_lote_items (N:M lote × produto) ───────────────────────
    op.create_table(
        "import_lote_items",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("lote_id", PG_UUID(as_uuid=True), sa.ForeignKey(f"{SCHEMA}.import_lotes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", PG_UUID(as_uuid=True), sa.ForeignKey(f"{SCHEMA}.import_products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("quantidade", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.UniqueConstraint("lote_id", "product_id", name="uq_import_lote_items_lote_product"),
        schema=SCHEMA,
    )

    # ── import_resumo ────────────────────────────────────────────────
    op.create_table(
        "import_resumo",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("data", sa.Date(), nullable=False),
        sa.Column("lote_id", PG_UUID(as_uuid=True), sa.ForeignKey(f"{SCHEMA}.import_lotes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("lote_nome", sa.String(50), nullable=True),
        sa.Column("saldo", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("obs", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )

    # ── Seed: 15 lançamentos do resumo original ──────────────────────
    # (Datas no formato YYYY-MM-DD vindas direto da planilha do operador.)
    seed_resumo = [
        ("2026-11-01", "11", Decimal("188006.93"), None),
        ("2026-12-01", "12", Decimal("64241.67"),  None),
        ("2026-12-01", "13", Decimal("64241.67"),  None),
        ("2026-12-01", "14", Decimal("64241.67"),  None),
        ("2026-12-02", "15", Decimal("49330.00"),  None),
        ("2026-12-02", "16", Decimal("49330.00"),  None),
        ("2026-01-30", "17", Decimal("47086.74"),  None),
        ("2026-01-30", "18", Decimal("47086.74"),  None),
        ("2026-01-30", "19", Decimal("47086.74"),  None),
        ("2026-01-30", "20", Decimal("47086.74"),  None),
        ("2026-02-26", "21", Decimal("48165.67"),  None),
        ("2026-02-26", "22", Decimal("48165.67"),  None),
        ("2026-02-26", "23", Decimal("48165.67"),  None),
        ("2026-03-26", "24", Decimal("62842.40"),  None),
        ("2026-04-24", None, Decimal("-3225.00"),  "215 malas 20→18"),
    ]
    rows = [
        f"('{data}', {'NULL' if lote is None else repr(lote)}, {saldo}, {'NULL' if obs is None else repr(obs)})"
        for (data, lote, saldo, obs) in seed_resumo
    ]
    op.execute(f"""
        INSERT INTO {SCHEMA}.import_resumo (data, lote_nome, saldo, obs)
        VALUES {", ".join(rows)}
    """)


def downgrade() -> None:
    op.drop_table("import_resumo", schema=SCHEMA)
    op.drop_table("import_lote_items", schema=SCHEMA)
    op.drop_table("import_lotes", schema=SCHEMA)
    op.drop_index("ix_import_products_sku", table_name="import_products", schema=SCHEMA)
    op.drop_table("import_products", schema=SCHEMA)
    op.drop_table("import_config", schema=SCHEMA)
