# ruff: noqa: E501, S608
"""vw_devolucoes: reescreve sem calculos financeiros

Substitui a versao derivada de vw_conciliacao_margens_marketplace_all por uma
query direta em bling_orders, mantendo apenas os campos de identidade,
situacao, produto e destinatario.

Os calculos de margem/frete/eventos nao sao necessarios para a gestao
de devolucoes.

Revision ID: 0092_vw_devolucoes_slim
Revises: 0091_vw_devolucoes
Create Date: 2026-05-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0092_vw_devolucoes_slim"
down_revision: str | None = "0091_vw_devolucoes"
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
    bo.cep_destino
FROM {SCHEMA}.bling_orders bo
LEFT JOIN {SCHEMA}.situacao_bling sb ON sb.id::text = bo.situacao
LEFT JOIN {SCHEMA}.stores s ON s.id = bo.store_id
WHERE bo.situacao IN ({", ".join(f"'{s}'" for s in _SITUACOES)})
""".strip()

_PREV_SQL = f"""
CREATE OR REPLACE VIEW {SCHEMA}.{VIEW} AS
SELECT
    v.*,
    bo.nome_destinatario,
    bo.cep_destino
FROM {SCHEMA}.vw_conciliacao_margens_marketplace_all v
LEFT JOIN {SCHEMA}.bling_orders bo ON bo.id = v.bling_order_item_id
WHERE v.situacao IN ({", ".join(f"'{s}'" for s in _SITUACOES)})
""".strip()


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(f'DROP VIEW IF EXISTS "{SCHEMA}"."{VIEW}" CASCADE')
    op.execute(_VIEW_SQL)


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(f'DROP VIEW IF EXISTS "{SCHEMA}"."{VIEW}" CASCADE')
    op.execute(_PREV_SQL)
