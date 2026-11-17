"""Produtos › Organizar: esconder da TELA o que é inválido (tabela à parte)

Eduardo, 17/11/2026 (decisões 3 e 4): o "Remover os N mortos" sai da tela e
fica só o ORGANIZAR — só ADM, só esconde (Produtos e Anúncios), dá para
desfazer por lote, por motivo ou por item. Nada é apagado e nenhum robô lê
estas tabelas.

- organizar_ocultos: uma marca por item escondido; `registro_id` aponta para
  products, product_links ou listings (sem FK: são 3 tabelas, e a leitura
  junta pela linha viva). `motivo` = a chave do Organizar (7, CHECK). Índice
  ÚNICO PARCIAL (tabela, registro_id) WHERE reexibido_em IS NULL: uma marca
  ativa por item. Fora do Histórico (`historico/sql.EXCLUIDAS`): a tabela já é
  a trilha.
- organizar_previas: os ids que a prévia mostrou (30 min), para o "Ocultar"
  gravar só o que a pessoa viu.

Sem ALTER em products, product_links ou listings (tabelas quentes) e sem
ALTER TYPE.

Revision ID: 0499_organizar_ocultos
Revises: 0498_carrefour_offer_write
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0499_organizar_ocultos"
down_revision: str | None = "0498_carrefour_offer_write"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
TABELAS = ("products", "product_links", "listings")
CHAVES = (
    "vinculo_morto",
    "produto_excluido_sem_anuncio",
    "produto_excluido_com_anuncio",
    "produto_inativo",
    "anuncio_loja_arquivada",
    "anuncio_encerrado",
    "anuncio_produto_excluido",
)


def _in(coluna: str, valores: tuple[str, ...]) -> str:
    return f"{coluna} IN ({', '.join(repr(v) for v in valores)})"


def upgrade() -> None:
    # Só tabela nova (sem ALTER em tabela quente); o limite vale para a FK em users.
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.create_table(
        "organizar_ocultos",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tabela", sa.String(20), nullable=False),
        sa.Column("registro_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("motivo", sa.String(40), nullable=False),
        sa.Column("lote_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("oculto_por", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "oculto_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("reexibido_por", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reexibido_em", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organizar_ocultos")),
        sa.ForeignKeyConstraint(
            ["oculto_por"],
            [f"{SCHEMA}.users.id"],
            name=op.f("fk_organizar_ocultos_oculto_por_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["reexibido_por"],
            [f"{SCHEMA}.users.id"],
            name=op.f("fk_organizar_ocultos_reexibido_por_users"),
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(_in("tabela", TABELAS), name=op.f("ck_organizar_ocultos_tabela")),
        sa.CheckConstraint(_in("motivo", CHAVES), name=op.f("ck_organizar_ocultos_motivo")),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_organizar_ocultos_ativo",
        "organizar_ocultos",
        ["tabela", "registro_id"],
        unique=True,
        postgresql_where=sa.text("reexibido_em IS NULL"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_organizar_ocultos_lote", "organizar_ocultos", ["lote_id"], schema=SCHEMA
    )
    op.create_table(
        "organizar_previas",
        sa.Column(
            "token",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("criado_por", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expira_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("token", name=op.f("pk_organizar_previas")),
        sa.ForeignKeyConstraint(
            ["criado_por"],
            [f"{SCHEMA}.users.id"],
            name=op.f("fk_organizar_previas_criado_por_users"),
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )
    # Tabela nova nunca analisada + milhares de marcas no 1º "Ocultar" = plano
    # ruim até o autovacuum passar (Mala: 86 ms → 2,1 s, medido na cópia com os
    # dados reais). ANALYZE vazia já basta para o planejador medir pelo tamanho.
    op.execute(f"ANALYZE {SCHEMA}.organizar_ocultos")


def downgrade() -> None:
    op.drop_table("organizar_previas", schema=SCHEMA)
    op.drop_index("ix_organizar_ocultos_lote", table_name="organizar_ocultos", schema=SCHEMA)
    op.drop_index("uq_organizar_ocultos_ativo", table_name="organizar_ocultos", schema=SCHEMA)
    op.drop_table("organizar_ocultos", schema=SCHEMA)
