# ruff: noqa: E501, S608
"""vw_devolucoes: fallback store lookup via bling_store_id when store_id is NULL

Pedidos importados sem store_id vinculado ficavam com loja_nome = NULL e eram
excluídos pelo filtro do order-lookup (loja_nome IS NOT NULL). O JOIN agora usa
COALESCE(bo.store_id, <lookup por bling_store_id>) para resolver o store mesmo
quando store_id não foi preenchido na importação.

Revision ID: 0097_vw_devolucoes_store_fallback
Revises: 0096_cotacao_seed_initial
Create Date: 2026-05-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0097_vw_devolucoes_store_fallback"
down_revision: str | None = "0096_cotacao_seed_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
VIEW = "vw_devolucoes"

_SITUACOES = ("83957", "83960", "83961", "83966")

_VIEW_SQL = f"""
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
LEFT JOIN {SCHEMA}.stores s ON s.id = COALESCE(
    bo.store_id,
    (SELECT s2.id FROM {SCHEMA}.stores s2 WHERE s2.bling_store_id::text = bo.loja LIMIT 1)
)
WHERE bo.situacao IN ({", ".join(f"'{s}'" for s in _SITUACOES)})
""".strip()

# downgrade restores the previous version (without fallback)
_PREV_SQL = f"""
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


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(f'DROP VIEW IF EXISTS "{SCHEMA}"."{VIEW}" CASCADE')
    op.execute(_VIEW_SQL)


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(f'DROP VIEW IF EXISTS "{SCHEMA}"."{VIEW}" CASCADE')
    op.execute(_PREV_SQL)
