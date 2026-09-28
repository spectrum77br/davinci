"""product_links: vínculo morto (e limpeza dos duplicados)

Eduardo, 28/09/2026: "estamos deixando links mortos". Medido em produção:
10.276 vínculos em anúncio encerrado/excluído/bloqueado recebendo ~35 mil
tentativas de envio por dia, e aparecendo na tela como se estivessem normais.

- morto_desde / morto_motivo: o anúncio que o marketplace disse que acabou.
  Não recebe mais envio; sai sozinho 30 dias depois (varredura diária);
  volta a viver se reaparecer ativo. Os de hoje são marcados já, pelo último
  erro gravado (mesma regra de services/vinculo_saude.py) — contam 30 dias a
  partir de agora.
- Marca como morto ("duplicado: …") 226 vínculos ML "do anúncio inteiro" que
  convivem com vínculos por variação do mesmo anúncio (216 duplicavam o envio;
  10 apontavam para outro produto e brigavam com o da variação) e 1.886
  vínculos Amazon no formato antigo (ASIN no lugar do SKU) com o par atual do
  mesmo seller-sku. Param de receber envio já; saem na limpeza em lotes.
  NADA é apagado aqui (ver o comentário no upgrade).

Revision ID: 0333_vinculos_mortos
Revises: 0332_companies_proxy
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.services.vinculo_saude import SQL_MOTIVO_MORTO

revision: str = "0333_vinculos_mortos"
down_revision: str | None = "0332_companies_proxy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.add_column(
        "product_links",
        sa.Column("morto_desde", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column("product_links", sa.Column("morto_motivo", sa.Text(), nullable=True), schema=SCHEMA)
    op.create_index(
        "ix_product_links_morto_desde", "product_links", ["morto_desde"],
        schema=SCHEMA, postgresql_where=sa.text("morto_desde IS NOT NULL"),
    )

    op.execute(
        f"""
        UPDATE {SCHEMA}.product_links
           SET morto_desde = now(), morto_motivo = {SQL_MOTIVO_MORTO}
         WHERE ({SQL_MOTIVO_MORTO}) IS NOT NULL
        """
    )

    # Duplicados: MARCADOS como mortos (param de receber envio agora) em vez de
    # apagados aqui. Apagar vínculo zera sync_logs.product_link_id (FK SET
    # NULL) — 583 mil linhas para estes — e dentro da migration isso travaria
    # product_links por dezenas de minutos. Quem apaga é a limpeza em lotes
    # (vinculo_saude.apagar_mortos_em_lotes), sem travar nada.
    op.execute(
        f"""
        UPDATE {SCHEMA}.product_links a
           SET morto_desde = now(),
               morto_motivo = 'duplicado: o anúncio já tem vínculo por variação'
         WHERE a.platform = 'ml' AND coalesce(a.variation_id, '') = ''
           AND EXISTS (
             SELECT 1 FROM {SCHEMA}.product_links b
              WHERE b.platform = 'ml' AND b.integration_id = a.integration_id
                AND b.external_id = a.external_id AND coalesce(b.variation_id, '') <> ''
           )
        """
    )
    op.execute(
        f"""
        UPDATE {SCHEMA}.product_links a
           SET morto_desde = now(),
               morto_motivo = 'duplicado: formato antigo da Amazon (o atual continua)'
         WHERE a.platform = 'amazon' AND coalesce(a.variation_id, '') = ''
           AND a.external_id ~ '^B0[A-Z0-9]{{8}}$'
           AND EXISTS (
             SELECT 1 FROM {SCHEMA}.product_links b
              WHERE b.platform = 'amazon' AND b.integration_id = a.integration_id
                AND b.external_id = a.external_sku AND b.id <> a.id
           )
        """
    )


def downgrade() -> None:
    # Só as colunas saem (a migration não apaga vínculo).
    op.drop_index("ix_product_links_morto_desde", table_name="product_links", schema=SCHEMA)
    op.drop_column("product_links", "morto_motivo", schema=SCHEMA)
    op.drop_column("product_links", "morto_desde", schema=SCHEMA)
