# ruff: noqa: E501, S608
"""vw_devolucoes: inclui pedidos em manutenção (84677) no order-lookup

A view só expunha pedidos em devolução/problema/reembolso/erro de envio
(83957, 83960, 83961, 83966). Pedidos "em manutenção" (84677) — situação para a
qual o próprio fluxo de devolução move o pedido — não apareciam no order-lookup,
impedindo o usuário de adicioná-los na página de devoluções. Adiciona 84677 ao
filtro de situações.

Revision ID: 0112_vw_devolucoes_manutencao
Revises: 0111_bling_orders_uniq_bling_item_idx
Create Date: 2026-06-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0112_vw_devolucoes_manutencao"
down_revision: str | None = "0111_bling_orders_uniq_bling_item_idx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
VIEW = "vw_devolucoes"

# 84677 = "Em manutenção" (novo); demais já existiam.
_SITUACOES = ("83957", "83960", "83961", "83966", "84677")
# situações da versão anterior (downgrade)
_PREV_SITUACOES = ("83957", "83960", "83961", "83966")


def _view_sql(situacoes: tuple[str, ...]) -> str:
    return f"""
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
WHERE bo.situacao IN ({", ".join(f"'{s}'" for s in situacoes)})
""".strip()


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(f'DROP VIEW IF EXISTS "{SCHEMA}"."{VIEW}" CASCADE')
    op.execute(_view_sql(_SITUACOES))


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(f'DROP VIEW IF EXISTS "{SCHEMA}"."{VIEW}" CASCADE')
    op.execute(_view_sql(_PREV_SITUACOES))
