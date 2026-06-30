# ruff: noqa: E501, S608
"""Pré-expande variantes de pricing_products + desempate por variante mais específica

PERFORMANCE: vw_bling_pedidos casava SKU→segmento fazendo, para CADA item de
pedido, um full-scan de pricing_products + regexp_split_to_table da SKU + LIKE
fuzzy. No EXPLAIN ANALYZE do rebuild completo isso eram ~898 mil execuções de
regex (197 linhas × 5,7 variantes × ~4,5k itens) e ~70–130s do custo. A tabela
`pricing_product_variant` faz o split UMA vez (~1.114 linhas, mantida por
trigger), e o LATERAL passa a casar contra ela (indexada por variant_norm).

CORRETUDE: o match antigo (LIMIT 1 sem desempate determinístico) escolhia
arbitrariamente entre variantes empatadas — 302 SKUs casavam com segmentos
diferentes na mesma prioridade e o resultado podia mudar sozinho num VACUUM.
O novo ORDER BY adiciona `vlen DESC` (variante mais específica vence — ex.:
`b038.20.mala` vai pro segmento de `b038.20`, não o genérico `b038`) e
`segment_id` como desempate final determinístico. Resolve 300/302 corretamente;
muda 32 SKUs / 129 itens históricos do valor arbitrário → correto.

A função `conciliacao_margens_for_bling_id` e as views `ln`/`ln_all` referenciam
vw_bling_pedidos POR NOME → herdam a mudança automaticamente (sem regen).

a082 (SKU base em 2 segmentos por erro de cadastro) resolve via o desempate →
Catálogo>Acessórios. A linha duplicada em pricing_products (com 9 pricing_overrides)
deve ser limpa no app à parte.

Revision ID: 0162_pricing_product_variant_match
Revises: 0161_listings_integ_sku_platform_idx
Create Date: 2026-06-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0162_pricing_product_variant_match"
down_revision: str | None = "0161_listings_integ_sku_platform_idx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
ANCHOR = "LEFT JOIN LATERAL ( SELECT pp_1.segment_id"

_TABLE = f"""
CREATE TABLE IF NOT EXISTS "{SCHEMA}".pricing_product_variant (
  pricing_product_id uuid NOT NULL,
  segment_id uuid,
  variant_norm text NOT NULL,
  vlen int NOT NULL
);
"""

_POPULATE = f"""
TRUNCATE "{SCHEMA}".pricing_product_variant;
INSERT INTO "{SCHEMA}".pricing_product_variant (pricing_product_id, segment_id, variant_norm, vlen)
SELECT pp.id, pp.segment_id, lower(trim(vr)), length(trim(vr))
FROM "{SCHEMA}".pricing_products pp, LATERAL regexp_split_to_table(pp.sku::text, ',') vr
WHERE length(trim(vr)) > 0;
"""

_INDEX = f'CREATE INDEX IF NOT EXISTS ix_ppv_variant_norm ON "{SCHEMA}".pricing_product_variant(variant_norm);'

_TRIGGER_FN = f"""
CREATE OR REPLACE FUNCTION "{SCHEMA}".pricing_product_variant_sync() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
  IF TG_OP = 'DELETE' THEN
    DELETE FROM "{SCHEMA}".pricing_product_variant WHERE pricing_product_id = OLD.id;
    RETURN OLD;
  END IF;
  DELETE FROM "{SCHEMA}".pricing_product_variant WHERE pricing_product_id = NEW.id;
  INSERT INTO "{SCHEMA}".pricing_product_variant (pricing_product_id, segment_id, variant_norm, vlen)
  SELECT NEW.id, NEW.segment_id, lower(trim(vr)), length(trim(vr))
  FROM regexp_split_to_table(coalesce(NEW.sku,'')::text, ',') vr
  WHERE length(trim(vr)) > 0;
  RETURN NEW;
END;
$fn$;
"""

_TRIGGER = f"""
DROP TRIGGER IF EXISTS trg_pricing_product_variant_sync ON "{SCHEMA}".pricing_products;
CREATE TRIGGER trg_pricing_product_variant_sync
AFTER INSERT OR DELETE OR UPDATE OF sku, segment_id ON "{SCHEMA}".pricing_products
FOR EACH ROW EXECUTE FUNCTION "{SCHEMA}".pricing_product_variant_sync();
"""

_NEW_INNER = """ SELECT v.segment_id
           FROM davinci.pricing_product_variant v
          WHERE lower(wm.item_codigo) = v.variant_norm OR lower(wm.item_codigo) ~~ (v.variant_norm || '.%'::text) OR lower(wm.item_codigo) ~~ (v.variant_norm || '+%'::text)
          ORDER BY (
                CASE
                    WHEN lower(wm.item_codigo) = v.variant_norm THEN 1
                    WHEN lower(wm.item_codigo) ~~ (v.variant_norm || '+%'::text) THEN 2
                    ELSE 3
                END), v.vlen DESC, v.segment_id
         LIMIT 1"""


def _rewrite_view() -> None:
    bind = op.get_bind()
    sql = bind.execute(
        sa.text("SELECT pg_get_viewdef(CAST(:v AS regclass), true)"),
        {"v": f"{SCHEMA}.vw_bling_pedidos"},
    ).scalar_one()
    # Idempotente: se já está reescrita (sem o regex), não faz nada.
    if "pricing_product_variant" in sql:
        return
    i = sql.find(ANCHOR)
    if i < 0 or sql.count(ANCHOR) != 1:
        raise RuntimeError(
            f"0162: esperava 1 ocorrência do LATERAL de pricing match em "
            f"vw_bling_pedidos, achei {sql.count(ANCHOR)}"
        )
    popen = sql.find("(", i)
    depth = 0
    j = popen
    while j < len(sql):
        if sql[j] == "(":
            depth += 1
        elif sql[j] == ")":
            depth -= 1
            if depth == 0:
                break
        j += 1
    new_sql = sql[: popen + 1] + _NEW_INNER + sql[j:]
    op.execute(f'CREATE OR REPLACE VIEW "{SCHEMA}".vw_bling_pedidos AS {new_sql}')


def upgrade() -> None:
    op.execute(_TABLE)
    op.execute(_INDEX)
    op.execute(_POPULATE)
    op.execute(_TRIGGER_FN)
    op.execute(_TRIGGER)
    _rewrite_view()


def downgrade() -> None:
    # Não há rollback automático seguro da reescrita da view (a definição
    # original viria de uma migration anterior). Para reverter, regenerar
    # vw_bling_pedidos a partir da migration que a definiu por último e depois:
    op.execute(f'DROP TRIGGER IF EXISTS trg_pricing_product_variant_sync ON "{SCHEMA}".pricing_products')
    op.execute(f'DROP FUNCTION IF EXISTS "{SCHEMA}".pricing_product_variant_sync()')
    op.execute(f'DROP TABLE IF EXISTS "{SCHEMA}".pricing_product_variant')
