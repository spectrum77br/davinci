# ruff: noqa: E501, S608
"""vw_devolucoes: devolucoes/problemas com CEP e nome destinatario

View derivada de vw_conciliacao_margens_marketplace_all filtrada para os
status de pos-entrega que requerem acao logistica:

  - 83957 Aguardando Devolucao
  - 83960 Problemas
  - 83961 Aguardando Reembolso
  - 83966 Erro no Envio

Adiciona nome_destinatario e cep_destino via JOIN direto com bling_orders
(bling_order_item_id e o UUID PK da tabela).

Revision ID: 0091_vw_devolucoes
Revises: 0090_import_products_tsa_int
Create Date: 2026-05-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0091_vw_devolucoes"
down_revision: str | None = "0090_import_products_tsa_int"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
VIEW = "vw_devolucoes"

_SITUACOES = ("83957", "83960", "83961", "83966")

_VIEW_SQL = f"""
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
