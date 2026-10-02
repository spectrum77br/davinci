"""Carrefour como plataforma de cadastro manual de empresas e lojas.

Não adiciona integração nem regra de preços. Empresas com a configuração
padrão completa passam a oferecer Carrefour; listas restritas são preservadas.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0357_marketplace_carrefour"
down_revision: str | None = "0356_denuncia_casos_extra_compra_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
PREVIOUS_MARKETPLACES = (
    "ml", "shopee", "amazon", "aliexpress", "temu",
    "tiktok", "shein", "magalu", "site",
)


def _array(values: tuple[str, ...]) -> str:
    return "ARRAY[" + ",".join(f"'{value}'" for value in values) + "]::text[]"


def upgrade() -> None:
    # PG12+ aceita ADD VALUE em transação. Não usamos o novo valor do enum
    # antes do commit; enabled_marketplaces é text[], não marketplace[].
    op.execute(f'ALTER TYPE "{SCHEMA}".marketplace ADD VALUE IF NOT EXISTS \'carrefour\'')
    op.execute(
        f'ALTER TABLE "{SCHEMA}".companies ALTER COLUMN enabled_marketplaces '
        f'SET DEFAULT {_array((*PREVIOUS_MARKETPLACES, "carrefour"))}'
    )
    op.execute(
        f'UPDATE "{SCHEMA}".companies '  # noqa: S608 — identificadores e valores constantes
        "SET enabled_marketplaces = array_append(enabled_marketplaces, 'carrefour') "
        f"WHERE enabled_marketplaces @> {_array(PREVIOUS_MARKETPLACES)} "
        "AND NOT ('carrefour' = ANY(enabled_marketplaces))"
    )


def downgrade() -> None:
    op.execute(
        f'ALTER TABLE "{SCHEMA}".companies ALTER COLUMN enabled_marketplaces '
        f'SET DEFAULT {_array(PREVIOUS_MARKETPLACES)}'
    )
    op.execute(
        f'UPDATE "{SCHEMA}".companies '  # noqa: S608 — identificadores e valores constantes
        "SET enabled_marketplaces = array_remove(enabled_marketplaces, 'carrefour') "
        "WHERE 'carrefour' = ANY(enabled_marketplaces)"
    )
    # PostgreSQL não remove valores de enum sem recriar o tipo. Mantemos o
    # valor para preservar lojas já cadastradas em vez de apagar seus dados.
