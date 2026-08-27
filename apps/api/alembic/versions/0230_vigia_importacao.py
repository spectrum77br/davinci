"""Tabela do vigia de importação — pedido pago no marketplace que não caiu no Bling.

Eduardo (2026-08-27): "hoje tem alguns pedidos, que nao caem em pedidos de
vendas automaticos e bem raro mais acontece, ai temos que ir em importar
vendas manualmente que e o canal multi loja e achar esse pedido [...] a
gente ja importar? para nao correr o risco de atrasar?".

A API pública do Bling v3 NÃO expõe a tela "Importar vendas" do canal multi
loja (verificado no OpenAPI oficial da referência, 162 endpoints,
2026-08-27). Então o vigia (services/vigia_importacao.py) olha pelo outro
lado: busca os pedidos PAGOS direto na API do marketplace (fase 1: Mercado
Livre) e confere se cada um já existe no espelho bling_orders (numeroloja).
Pedido pago que não apareceu vira aviso no Threema pra equipe importar
manualmente. Esta tabela é o estado anti-spam de cada pedido detectado.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0230_vigia_importacao"
down_revision: str | None = "0229_pricing_prioridade_estoque"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "vigia_importacao",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("plataforma", sa.Text(), nullable=False),
        sa.Column("conta", sa.Text(), nullable=True),
        sa.Column("numero_loja", sa.Text(), nullable=False),
        sa.Column("pack_id", sa.Text(), nullable=True),
        sa.Column("pago_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "detectado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ultima_verificacao", sa.DateTime(timezone=True), nullable=True),
        sa.Column("avisado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolvido_em", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "plataforma", "numero_loja", name="uq_vigia_importacao_plataforma_numero"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_vigia_importacao_resolvido_em",
        "vigia_importacao",
        ["resolvido_em"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_vigia_importacao_resolvido_em", table_name="vigia_importacao", schema=SCHEMA
    )
    op.drop_table("vigia_importacao", schema=SCHEMA)
