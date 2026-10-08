"""Redes Sociais: a conta da Shopee Vídeo aponta pra UMA loja (integração)

Eduardo, 08/10/2026: a Shopee Vídeo entra como mais uma rede em Marketing ›
Criativos, começando pela loja Barbosa (Uranyx). Diferente do Instagram ou do
YouTube, o vídeo da Shopee é publicado DENTRO de uma loja do marketplace e
leva o anúncio daquela loja junto — então a conta precisa saber qual é a loja:
é por ela que o robô acha o anúncio certo (product_links da integração) e é
ela que a autorização da Shopee tem de confirmar (o shop_id do retorno tem de
ser o da integração, senão alguém autorizou outra loja).

  • `redes_sociais.integration_id` (FK integrations, ON DELETE SET NULL,
    nulável). Só a Shopee usa; nas outras redes fica NULL.
  • único PARCIAL `uq_redes_sociais_integration_id`: uma conta de Shopee Vídeo
    por loja. Duas contas na mesma loja dobrariam o teto do dia sem ninguém ver.

Nada mais muda no banco: a plataforma é String (sem enum no Postgres), a
credencial do app de vídeo (partner_id/partner_key/tokens) vai cifrada em
`redes_sociais_tokens.token_enc`, e o anúncio escolhido + o aigc_label vão no
`marketing_postagens.opcoes` (JSONB).

Só ADD COLUMN nulável + índice pequeno (a tabela tem dezenas de linhas).

Revision ID: 0383_redes_sociais_loja_shopee
Revises: 0382_conferencia_plataformas
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0383_redes_sociais_loja_shopee"
down_revision: str | None = "0382_conferencia_plataformas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
LOCK_TIMEOUT = "10s"

TABELA = "redes_sociais"
# Mesmos nomes do model (naming convention de app/models/base.py).
FK = "fk_redes_sociais_integration_id_integrations"
UQ = "uq_redes_sociais_integration_id"


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.add_column(
        TABELA,
        sa.Column("integration_id", pg.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        FK,
        TABELA,
        "integrations",
        ["integration_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )
    op.create_index(
        UQ,
        TABELA,
        ["integration_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("integration_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(UQ, table_name=TABELA, schema=SCHEMA)
    op.drop_constraint(FK, TABELA, schema=SCHEMA, type_="foreignkey")
    op.drop_column(TABELA, "integration_id", schema=SCHEMA)
