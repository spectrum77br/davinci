# ruff: noqa: E501, S608
"""Devoluções: painel novo — o pedido só sai da lista no botão Resolver

Vinicius, 16/11/2026: o painel novo de Devoluções "pega tudo que cair em ag
devolução" e o pedido só sai quando alguém clica em Resolver ("igual do
chamado"). Guarda a resposta em `devolucao_rastreio` (grão de pedido):
`resolvido_at`, `resolvido_por`, `resolvido_como`.

Só colunas novas (o painel antigo não lê nenhuma delas e continua igual).

Largada: quem já passou por Aguardando Devolução e HOJE não está mais em
Aguardando Devolução (83957) nem em Manutenção (84677) nasce resolvido — a
lista nova começa igual à de hoje (Acompanhamento + Fraude) mais os pedidos
em Manutenção.

Revision ID: 0483_devolucao_painel_resolver
Revises: 0482_logistica_tracken
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0483_devolucao_painel_resolver"
down_revision: str | None = "0482_logistica_tracken"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
TABELA = "devolucao_rastreio"
LARGADA = "antes do painel novo"


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.add_column(
        TABELA, sa.Column("resolvido_at", sa.DateTime(timezone=True), nullable=True), schema=SCHEMA
    )
    op.add_column(
        TABELA,
        sa.Column(
            "resolvido_por",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        schema=SCHEMA,
    )
    op.add_column(TABELA, sa.Column("resolvido_como", sa.Text(), nullable=True), schema=SCHEMA)
    op.execute(
        f"""
        INSERT INTO "{SCHEMA}".{TABELA} (pedido_bling, resolvido_at, resolvido_como)
        SELECT bo.numero, now(), '{LARGADA}'
        FROM "{SCHEMA}".bling_orders bo
        WHERE bo.numero IS NOT NULL AND btrim(bo.numero) <> ''
        GROUP BY bo.numero
        HAVING bool_or(bo.aguardando_devolucao_data IS NOT NULL)
           AND NOT bool_or(COALESCE(bo.situacao, '') IN ('83957', '84677'))
        ON CONFLICT (pedido_bling) DO UPDATE
            SET resolvido_at = EXCLUDED.resolvido_at,
                resolvido_como = EXCLUDED.resolvido_como
            WHERE "{SCHEMA}".{TABELA}.resolvido_at IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column(TABELA, "resolvido_como", schema=SCHEMA)
    op.drop_column(TABELA, "resolvido_por", schema=SCHEMA)
    op.drop_column(TABELA, "resolvido_at", schema=SCHEMA)
