"""product_links: vínculo morto (e limpeza dos duplicados)

Eduardo, 28/09/2026: "estamos deixando links mortos". Medido em produção:
10.276 vínculos em anúncio encerrado/excluído/bloqueado recebendo ~35 mil
tentativas de envio por dia, e aparecendo na tela como se estivessem normais.

- morto_desde / morto_motivo: o anúncio que o marketplace disse que acabou.
  Não recebe mais envio; sai sozinho 30 dias depois (varredura diária);
  volta a viver se reaparecer ativo. Os de hoje são marcados já, pelo último
  erro gravado (mesma regra de services/vinculo_saude.py) — contam 30 dias a
  partir de agora.
- Marca como morto ("duplicado: …") os vínculos ML "do anúncio inteiro" cujo
  anúncio tem vínculo por VARIAÇÃO funcionando (último envio ok) — ~30; os
  outros 190 são de conta arquivada/sem acesso ou o da variação é que está
  falhando, e ficam como estão — e 1.886 vínculos Amazon no formato antigo
  (ASIN no lugar do SKU) com o par atual do mesmo seller-sku. Param de
  receber envio já; saem na limpeza em lotes. NADA é apagado aqui.
- sync_logs.product_link_id perde a FK (ON DELETE SET NULL): o log guarda o
  id do vínculo apagado. sync_logs nunca é podado, e a FK obrigava a
  reescrever ~1,6 milhão de linhas do histórico para apagar os mortos
  (medido pela revisão de 28/09: 21 min de trava e 22 GB de WAL numa cópia).

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

    op.drop_constraint(
        "sync_logs_product_link_id_fkey", "sync_logs", schema=SCHEMA, type_="foreignkey"
    )

    # Duplicados: MARCADOS como mortos (param de receber envio agora); quem
    # apaga é a limpeza em lotes (vinculo_saude.apagar_mortos_em_lotes).
    # ML: só quando a variação do mesmo anúncio está funcionando — se é ela que
    # falha, o do anúncio inteiro pode ser o certo (anúncio que perdeu as
    # variações), e fica.
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
                AND b.last_sync_status = 'ok' AND b.morto_desde IS NULL
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
    # A migration não apaga vínculo. Para a FK voltar, o log de vínculo que já
    # foi apagado depois dela precisa ficar sem dono (como a FK fazia).
    op.execute(
        f"""
        UPDATE {SCHEMA}.sync_logs s SET product_link_id = NULL
         WHERE s.product_link_id IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM {SCHEMA}.product_links l WHERE l.id = s.product_link_id)
        """
    )
    op.create_foreign_key(
        "sync_logs_product_link_id_fkey", "sync_logs", "product_links",
        ["product_link_id"], ["id"], source_schema=SCHEMA, referent_schema=SCHEMA,
        ondelete="SET NULL",
    )
    op.drop_index("ix_product_links_morto_desde", table_name="product_links", schema=SCHEMA)
    op.drop_column("product_links", "morto_motivo", schema=SCHEMA)
    op.drop_column("product_links", "morto_desde", schema=SCHEMA)
