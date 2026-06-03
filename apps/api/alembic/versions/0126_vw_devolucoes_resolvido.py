# ruff: noqa: E501, S608
"""vw_devolucoes: inclui pedidos resolvidos (545902) no order-lookup

A view expunha pedidos em devolução/problema/reembolso/erro de envio/manutenção
(83957, 83960, 83961, 83966, 84677), mas não os pedidos "Resolvido" (545902) —
situação para a qual o próprio fluxo de devolução move o pedido ao concluir.
Assim, depois de resolvido o pedido sumia do order-lookup e não dava mais para
reabri-lo na página de devoluções. Adiciona 545902 ao filtro de situações.

Revision ID: 0126_vw_devolucoes_resolvido
Revises: 0125_vw_bling_pedidos_min_margin_floor_default
Create Date: 2026-06-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0126_vw_devolucoes_resolvido"
down_revision: str | None = "0125_vw_bling_pedidos_min_margin_floor_default"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
VIEW = "vw_devolucoes"

# 545902 = "Resolvido" (novo); demais já existiam.
_SITUACOES = ("83957", "83960", "83961", "83966", "84677", "545902")
# situações da versão anterior (downgrade)
_PREV_SITUACOES = ("83957", "83960", "83961", "83966", "84677")


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
