"""backfill: bases da aba Kit p/ produtos celular criados após o seed 0117

Quando um produto de Celular é criado na aba Importação ele passa a virar
automaticamente uma LINHA (base) na aba Kit (ver
`_maybe_create_kit_base_for_product` em routers/importacao.py). Esta migration
fecha a lacuna dos produtos que já existiam ANTES dessa regra: 8 aparelhos
celular sem base (Oukitel C68 Plus ×4, iPad '.sp' ×4).

Acessórios (fone/carregador/aspirador/roteador) NÃO entram: são componentes
de kit (as variações a001/a003/a004…), não produtos-base. A heurística é a
mesma do runtime — é aparelho ⇔ modelo_bling tem ' - <Cor>'. Todas as bases
celular existentes seguem esse padrão; os únicos produtos sem ' - ' são os
acessórios, que ficam de fora.

Derivação idêntica ao seed 0117 (1 produto → 1 base pelo SKU completo, cor
extraída do trecho após o último ' - '). `ordem` continua MAX+1 na categoria.

Idempotente: INSERT ... WHERE NOT EXISTS pelo sku_base (UNIQUE global).
Downgrade: no-op — backfill é aditivo e o operador pode já ter marcado
células dessas linhas; remover apagaria em cascata as marks (FK ON DELETE
CASCADE). Limpeza, se necessária, é manual.

Revision ID: 0164_kit_celular_backfill_missing_bases
Revises: 0163_ingest_bling_order_job_type
Create Date: 2026-07-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0164_kit_celular_backfill_missing_bases"
down_revision: str | None = "0163_ingest_bling_order_job_type"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(
        rf"""
        INSERT INTO {SCHEMA}.import_kit_bases
            (categoria, modelo_bling, sku_base, cor, ordem)
        SELECT
            'celular',
            p.modelo_bling,
            p.sku,
            UPPER(LEFT(regexp_replace(p.modelo_bling, '^.*\s-\s', ''), 1))
                || SUBSTRING(regexp_replace(p.modelo_bling, '^.*\s-\s', '') FROM 2),
            (
                SELECT COALESCE(MAX(ordem), 0)
                FROM {SCHEMA}.import_kit_bases
                WHERE categoria = 'celular'
            ) + row_number() OVER (ORDER BY p.sku)
        FROM {SCHEMA}.import_products p
        WHERE p.categoria = 'celular'
          AND p.modelo_bling LIKE '% - %'
          AND NOT EXISTS (
              SELECT 1 FROM {SCHEMA}.import_kit_bases b
              WHERE b.sku_base = p.sku
          )
        """  # noqa: S608
    )


def downgrade() -> None:
    # No-op: backfill aditivo. Ver docstring — reverter apagaria marks em
    # cascata se o operador já tiver usado essas linhas.
    pass
