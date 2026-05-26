# ruff: noqa: E501, S608
"""bling_orders — endereço de entrega completo (logradouro, número, complemento, bairro, cidade, UF).

Adiciona os seis campos restantes de transporte.enderecoEntrega que faltavam
após a migration 0088 (que só trouxe nome e CEP), e recria vw_devolucoes
com esses novos campos.

Revision ID: 0094_bling_orders_endereco_destino
Revises: 0093_merge_bling_sync_devolucoes
Create Date: 2026-05-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0094_bling_orders_endereco_destino"
down_revision: str | None = "0093_merge_bling_sync_devolucoes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
VIEW = "vw_devolucoes"

_SITUACOES = ("83957", "83960", "83961", "83966")

_NEW_VIEW_SQL = f"""
CREATE OR REPLACE VIEW {SCHEMA}.{VIEW} AS
SELECT
    bo.id                       AS bling_order_item_id,
    bo.bling_id,
    bo.numero                   AS pedido_bling,
    bo.numeroloja               AS pedido_marketplace,
    bo.data,
    bo.situacao,
    sb.nome                     AS situacao_nome,
    bo.loja                     AS bling_loja_id,
    s.apelido_override          AS loja_nome,
    s.marketplace::text         AS plataforma_bling,
    bo.item_codigo              AS sku,
    bo.item_produto_id,
    bo.item_descricao           AS produto,
    bo.item_quantidade          AS quantidade,
    bo.itemvalor                AS item_valor_original,
    bo.item_desconto,
    bo.categoria_id,
    bo.categoria_nome,
    bo.verificado,
    bo.aprovado_por,
    bo.observacao,
    bo.nome_destinatario,
    bo.cep_destino,
    bo.endereco_destino,
    bo.numero_destino,
    bo.complemento_destino,
    bo.bairro_destino,
    bo.cidade_destino,
    bo.uf_destino
FROM {SCHEMA}.bling_orders bo
LEFT JOIN {SCHEMA}.situacao_bling sb ON sb.id::text = bo.situacao
LEFT JOIN {SCHEMA}.stores s ON s.id = bo.store_id
WHERE bo.situacao IN ({", ".join(f"'{s}'" for s in _SITUACOES)})
""".strip()

_OLD_VIEW_SQL = f"""
CREATE OR REPLACE VIEW {SCHEMA}.{VIEW} AS
SELECT
    bo.id                       AS bling_order_item_id,
    bo.bling_id,
    bo.numero                   AS pedido_bling,
    bo.numeroloja               AS pedido_marketplace,
    bo.data,
    bo.situacao,
    sb.nome                     AS situacao_nome,
    bo.loja                     AS bling_loja_id,
    s.apelido_override          AS loja_nome,
    s.marketplace::text         AS plataforma_bling,
    bo.item_codigo              AS sku,
    bo.item_produto_id,
    bo.item_descricao           AS produto,
    bo.item_quantidade          AS quantidade,
    bo.itemvalor                AS item_valor_original,
    bo.item_desconto,
    bo.categoria_id,
    bo.categoria_nome,
    bo.verificado,
    bo.aprovado_por,
    bo.observacao,
    bo.nome_destinatario,
    bo.cep_destino
FROM {SCHEMA}.bling_orders bo
LEFT JOIN {SCHEMA}.situacao_bling sb ON sb.id::text = bo.situacao
LEFT JOIN {SCHEMA}.stores s ON s.id = bo.store_id
WHERE bo.situacao IN ({", ".join(f"'{s}'" for s in _SITUACOES)})
""".strip()

_NEW_COLS = [
    "endereco_destino",
    "numero_destino",
    "complemento_destino",
    "bairro_destino",
    "cidade_destino",
    "uf_destino",
]


def upgrade() -> None:
    for col in _NEW_COLS:
        op.add_column(
            "bling_orders",
            sa.Column(col, sa.Text(), nullable=True),
            schema=SCHEMA,
        )
    op.execute(f'DROP VIEW IF EXISTS "{SCHEMA}"."{VIEW}" CASCADE')
    op.execute(_NEW_VIEW_SQL)


def downgrade() -> None:
    op.execute(f'DROP VIEW IF EXISTS "{SCHEMA}"."{VIEW}" CASCADE')
    op.execute(_OLD_VIEW_SQL)
    for col in reversed(_NEW_COLS):
        op.drop_column("bling_orders", col, schema=SCHEMA)
