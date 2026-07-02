# ruff: noqa: E501, S608
"""product_links: dedup de encoding legado (Shopee item_model / Amazon ASIN-keyed)

Dois caminhos de código gravaram o MESMO anúncio com identidades diferentes,
burlando a uq_product_links_identity e duplicando o estoque na tela de
produtos ("Luminin 2x mesmo estoque"):

- Shopee: a promoção via `listings` (tabela sem coluna de variação) gravava
  external_id="item_model" com variation_id vazio; o auto_link via API grava
  o canônico (external_id=item, variation_id=model). 392 linhas legadas, 360
  com gêmeo canônico.
- Amazon: linhas legadas do cutover com external_id=ASIN e variation vazia;
  o auto_link atual grava (external_id=seller-sku, variation_id=ASIN). Também
  há seller-sku relistado com 2 ASINs — o push é chaveado por seller-sku, então
  as cópias são redundantes.

Regra: apaga a linha legada quando o gêmeo canônico existe (mesma
integração/anúncio); senão RE-ENCODA pro formato canônico (não perde o link —
apagar deixaria o anúncio sem sync até o próximo auto_link). Duplicatas de
anúncios DISTINTOS (ML multi-anúncio com mesmo SKU) são reais e ficam intactas.

sync_logs.product_link_id tem FK ON DELETE SET NULL — deletar links é seguro.

⚠️ Rodar com os workers PARADOS: um sync_all em andamento segura row-locks de
product_links e os DELETEs ficam presos (visto no dry-run de 2/jul). No deploy
normal (docker compose up --build) os workers reiniciam junto, então ok.

Revision ID: 0167_product_links_dedup_encoding
Revises: 0166_bling_orders_data_tz_fix
Create Date: 2026-07-02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0167_product_links_dedup_encoding"
down_revision: str | None = "0166_bling_orders_data_tz_fix"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

_SHOPEE_DELETE_TWINNED = f"""
    DELETE FROM "{SCHEMA}"."product_links" a
    WHERE a.platform = 'shopee'
      AND a.external_id ~ '^[0-9]+_[0-9]+$'
      AND COALESCE(a.variation_id, '') = ''
      AND EXISTS (
          SELECT 1 FROM "{SCHEMA}"."product_links" b
          WHERE b.id <> a.id
            AND b.integration_id = a.integration_id
            AND b.platform = 'shopee'
            AND b.external_id = split_part(a.external_id, '_', 1)
            AND COALESCE(b.variation_id, '') = split_part(a.external_id, '_', 2)
      )
"""

# Sem gêmeo canônico: re-encoda em vez de apagar (mantém o anúncio sincronizando).
_SHOPEE_REENCODE_ORPHANS = f"""
    UPDATE "{SCHEMA}"."product_links"
    SET variation_id = split_part(external_id, '_', 2),
        external_id = split_part(external_id, '_', 1),
        updated_at = NOW()
    WHERE platform = 'shopee'
      AND external_id ~ '^[0-9]+_[0-9]+$'
      AND COALESCE(variation_id, '') = ''
"""

# Linha legada chaveada por ASIN cujo ASIN aparece como variation de uma linha
# canônica (mesmo produto, mesma conta) = mesmo anúncio duas vezes.
_AMAZON_DELETE_ASIN_KEYED = f"""
    DELETE FROM "{SCHEMA}"."product_links" a
    WHERE a.platform = 'amazon'
      AND COALESCE(a.variation_id, '') = ''
      AND EXISTS (
          SELECT 1 FROM "{SCHEMA}"."product_links" b
          WHERE b.id <> a.id
            AND b.integration_id = a.integration_id
            AND b.platform = 'amazon'
            AND b.product_id = a.product_id
            AND b.variation_id = a.external_id
      )
"""

# Mesmo seller-sku (external_id) repetido pro mesmo produto/conta com ASINs
# (variation_id) diferentes — relistagem. O push de estoque é keyed por
# seller-sku, então só a mais recente importa; mantém last_sync_at mais novo.
_AMAZON_DELETE_RELISTED = f"""
    DELETE FROM "{SCHEMA}"."product_links" a
    USING (
        SELECT id, ROW_NUMBER() OVER (
            PARTITION BY integration_id, product_id, external_id
            ORDER BY last_sync_at DESC NULLS LAST, updated_at DESC, id
        ) AS rn
        FROM "{SCHEMA}"."product_links"
        WHERE platform = 'amazon'
    ) ranked
    WHERE a.id = ranked.id AND ranked.rn > 1
"""

_COUNT_DUPES = f"""
    SELECT COALESCE(SUM(n - 1), 0) FROM (
        SELECT COUNT(*) AS n FROM "{SCHEMA}"."product_links"
        GROUP BY integration_id, product_id, platform
        HAVING COUNT(*) > 1
    ) t
"""


def upgrade() -> None:
    bind = op.get_bind()
    before = bind.execute(sa.text(_COUNT_DUPES)).scalar()
    r1 = bind.execute(sa.text(_SHOPEE_DELETE_TWINNED))
    r2 = bind.execute(sa.text(_SHOPEE_REENCODE_ORPHANS))
    r3 = bind.execute(sa.text(_AMAZON_DELETE_ASIN_KEYED))
    r4 = bind.execute(sa.text(_AMAZON_DELETE_RELISTED))
    after = bind.execute(sa.text(_COUNT_DUPES)).scalar()
    print(
        f"0167: shopee del={r1.rowcount} reencode={r2.rowcount} | "
        f"amazon asin-keyed del={r3.rowcount} relisted del={r4.rowcount} | "
        f"links excedentes {before} -> {after} (restantes = multi-anúncio real)"
    )


def downgrade() -> None:
    # Limpeza de dados sem inversa — as linhas apagadas eram duplicatas do
    # mesmo anúncio; o re-encode Shopee não é revertido de propósito (o
    # formato composto era o bug).
    pass
