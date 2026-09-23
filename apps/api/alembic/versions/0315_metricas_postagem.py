"""marketing_postagem_metricas — o que cada vídeo publicado rendeu

Eduardo, 23/09/2026: ver as views e interações por marca no DaVinci, com o
total somado e o detalhe de cada rede. Por VÍDEO publicado por aqui, não o
número geral do canal — pedido explícito dele, e o que a tabela-mãe permite:
`marketing_postagens` só contém o que saiu pelo DaVinci.

RETRATO DATADO, não linha sobrescrita. As três redes devolvem número
ACUMULADO ("este vídeo tem 400 views"), nunca o do dia. Com UPDATE por cima,
"quanto rendeu esta semana" não tem resposta — e é metade do valor da tela.
Com um retrato por dia, a diferença entre dois dias é uma subtração.

Nome escolhido a dedo: JÁ EXISTE uma `marketing_metrics` no schema, de anúncio
PAGO de marketplace (Shopee/ML/Amazon), presa a `MarketingAccount` e sem marca.
Chamar esta de "metricas" colidiria com aquela na cabeça de quem ler depois.

O DDL abaixo foi COPIADO do que o modelo gera (CreateTable), não escrito à mão
— inclusive os nomes das FKs. Foi escrevendo nome de constraint no chute que
produção já divergiu do que os testes constroem com create_all.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0315_metricas_postagem"
down_revision: str | None = "0314_limpa_entregas_vazias"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "marketing_postagem_metricas",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("postagem_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Dia da coleta. Um retrato por dia por postagem (unique embaixo).
        sa.Column("dia", sa.DateTime(timezone=True), nullable=False),
        # Snapshot: sobrevive à conta sair do cadastro, igual a postagem faz.
        sa.Column("plataforma", sa.String(32), nullable=False),
        sa.Column("conta", sa.Text(), nullable=True),
        sa.Column("marca_id", postgresql.UUID(as_uuid=True), nullable=True),
        # NULO = a rede não deu este número. ZERO = deu, e é zero. Somar
        # tratando nulo como zero é como a tela mentiria sem ninguém ver.
        sa.Column("views", sa.Integer(), nullable=True),
        sa.Column("curtidas", sa.Integer(), nullable=True),
        sa.Column("comentarios", sa.Integer(), nullable=True),
        sa.Column("compartilhamentos", sa.Integer(), nullable=True),
        sa.Column("salvamentos", sa.Integer(), nullable=True),
        sa.Column("alcance", sa.Integer(), nullable=True),
        # O que a rede respondeu, cru — pra quando um número parecer errado.
        sa.Column("bruto", postgresql.JSONB(), nullable=True),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_marketing_postagem_metricas"),
        sa.ForeignKeyConstraint(
            ["postagem_id"],
            [f"{SCHEMA}.marketing_postagens.id"],
            name="fk_marketing_postagem_metricas_postagem_id_marketing_postagens",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["marca_id"],
            [f"{SCHEMA}.marcas.id"],
            name="fk_marketing_postagem_metricas_marca_id_marcas",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("postagem_id", "dia", name="uq_metrica_postagem_dia"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_marketing_postagem_metricas_postagem_id",
        "marketing_postagem_metricas",
        ["postagem_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_marketing_postagem_metricas_marca_id",
        "marketing_postagem_metricas",
        ["marca_id"],
        schema=SCHEMA,
    )
    # A tela pergunta sempre "esta marca, neste período".
    op.create_index(
        "ix_metrica_marca_dia",
        "marketing_postagem_metricas",
        ["marca_id", "dia"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("marketing_postagem_metricas", schema=SCHEMA)
