"""atendimento: avaliações de venda (Shopee e Mercado Livre) com pendência e conversa

Eduardo, 02/10/2026 (PROJETO-COMUNICADOR.md, RF8; levantamento em
docs/atendimento-comunicador-levantamento.md §2.5 e §3.12): "traz as
avaliações das plataformas que der já". Entram a Shopee (`get_comment`, com
resposta pela API — desligada junto com o envio) e o Mercado Livre (a OPINIÃO
do produto, `/reviews/item`, que traz o `order_id`; sem resposta pela API).
TikTok, Magalu e Amazon ficam de fora (sem API de avaliação para o vendedor).

O nome `atendimento_avaliacoes` já é a nota da pessoa sobre a sugestão da IA:
a tabela que se estende é a `atendimento_avaliacoes_loja` (0346, 3.797 linhas
da Shopee em produção em 02/10), que já é genérica (plataforma, comentário,
pedido, estrelas, texto, resposta).

O que muda (só aditivo):
  • colunas novas em `atendimento_avaliacoes_loja`: `titulo`, `midia` (jsonb,
    []), `resposta_em`, `resposta_oculta`, `editavel`, `modelo_id`,
    `pode_responder` (false), `motivo_sem_resposta`, `pendente_desde`,
    `tratada_em`, `tratada_por` (FK users, SET NULL), `conversa_id` (FK
    conversas, SET NULL — nome à mão: pela convenção passaria de 63
    caracteres), `comprador_id`, `dados` (jsonb, {}) e `atualizado_em`;
  • índices: (plataforma, pedido), (integration_id, comprador_id),
    `conversa_id`, e o PARCIAL das pendentes (plataforma, pedido) WHERE
    `pendente_desde IS NOT NULL` — o fato da etiqueta lê só elas;
  • as linhas que já existem são todas da Shopee, que responde pela API:
    `pode_responder = true` nelas (UPDATE de ~3.800 linhas). Nenhuma vira
    pendente aqui: quem decide é `services/atendimento/avaliacoes.py`, depois
    de reler a avaliação na Shopee.

ADD COLUMN com default constante (ou `now()`, estável) não reescreve a tabela
(Postgres 11+). `lock_timeout` de 3 s, como a 0346/0347/0353: a api e o job
de hora em hora gravam nesta tabela. `tests/test_atendimento_migration.py`
roda 0346 + 0347 + 0353 + esta num schema descartável e compara o catálogo
com o model.

O downgrade apaga os índices e as colunas novas (a pendência, a mídia e as
opiniões do ML lidas se perdem; as linhas da Shopee ficam como eram).

Revision ID: 0358_atendimento_avaliacoes
Revises: 0357_marketplace_carrefour
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0358_atendimento_avaliacoes"
down_revision: str | None = "0357_marketplace_carrefour"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
TABELA = "atendimento_avaliacoes_loja"

_COLUNAS = (
    "titulo",
    "midia",
    "resposta_em",
    "resposta_oculta",
    "editavel",
    "modelo_id",
    "pode_responder",
    "motivo_sem_resposta",
    "pendente_desde",
    "tratada_em",
    "tratada_por",
    "conversa_id",
    "comprador_id",
    "dados",
    "atualizado_em",
)
_INDICES = (
    "ix_atendimento_avaliacoes_loja_pendentes",
    "ix_atendimento_avaliacoes_loja_integration_id_comprador_id",
    "ix_atendimento_avaliacoes_loja_plataforma_pedido",
    "ix_atendimento_avaliacoes_loja_conversa_id",
)


def upgrade() -> None:
    # ALTER TABLE pega AccessExclusiveLock na tabela e as FKs novas pegam
    # ShareRowExclusiveLock em `atendimento_conversas` e `users`, com a api,
    # o sync e o job de hora em hora rodando: sem teto, uma transação longa
    # faria a migration esperar e enfileirar todo mundo atrás dela.
    op.execute("SET lock_timeout = '3s'")

    colunas = [
        sa.Column("titulo", sa.Text(), nullable=True),
        sa.Column("midia", pg.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("resposta_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resposta_oculta", sa.Boolean(), nullable=True),
        sa.Column("editavel", sa.String(32), nullable=True),
        sa.Column("modelo_id", sa.String(64), nullable=True),
        sa.Column("pode_responder", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("motivo_sem_resposta", sa.Text(), nullable=True),
        sa.Column("pendente_desde", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tratada_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tratada_por", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("conversa_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("comprador_id", sa.String(128), nullable=True),
        sa.Column("dados", pg.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]
    for coluna in colunas:
        op.add_column(TABELA, coluna, schema=SCHEMA)

    op.create_foreign_key(
        "fk_atendimento_avaliacoes_loja_tratada_por_users",
        TABELA,
        "users",
        ["tratada_por"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )
    # Nome à mão: pela convenção passaria dos 63 caracteres do Postgres.
    op.create_foreign_key(
        "fk_atendimento_avaliacoes_loja_conversa",
        TABELA,
        "atendimento_conversas",
        ["conversa_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )

    op.create_index(
        "ix_atendimento_avaliacoes_loja_conversa_id", TABELA, ["conversa_id"], schema=SCHEMA
    )
    op.create_index(
        "ix_atendimento_avaliacoes_loja_plataforma_pedido",
        TABELA,
        ["plataforma", "pedido"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_atendimento_avaliacoes_loja_integration_id_comprador_id",
        TABELA,
        ["integration_id", "comprador_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_atendimento_avaliacoes_loja_pendentes",
        TABELA,
        ["plataforma", "pedido"],
        schema=SCHEMA,
        postgresql_where=sa.text("pendente_desde IS NOT NULL"),
    )

    # Todas as linhas de antes são da Shopee (o job de hora em hora e a
    # importação), e a Shopee responde avaliação pela API (`reply_comment`).
    op.execute(
        f"UPDATE {SCHEMA}.{TABELA} SET pode_responder = true "  # noqa: S608 — nomes fixos daqui
        "WHERE plataforma = 'shopee' AND pode_responder = false"
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    for nome in _INDICES:
        op.drop_index(nome, table_name=TABELA, schema=SCHEMA)
    # DROP COLUMN leva junto as FKs da coluna.
    for coluna in reversed(_COLUNAS):
        op.drop_column(TABELA, coluna, schema=SCHEMA)
