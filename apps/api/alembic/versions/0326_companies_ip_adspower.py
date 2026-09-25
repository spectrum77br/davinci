"""companies: qual IP está de fato aplicado no AdsPower

Eduardo (25/09/2026): "quando colocarmos um ip novo para a empresa, já vai
diretamente para o ads power". O AdsPower só responde no Mac, então quem aplica
é um serviço local que busca as pendências aqui e devolve o resultado. Estas
colunas guardam esse resultado:

  ip_adspower       o IP que o serviço confirmou nos perfis da empresa
  ip_adspower_em    quando confirmou
  ip_adspower_erro  por que não conseguiu (vazio quando deu certo)

Pendente = `ip` preenchido e diferente de `ip_adspower`.

As empresas que JÁ têm IP nascem marcadas como aplicadas. Eduardo: "não quero
que atualize nada, para não dar pau, só preciso que fique automático quando eu
colocar o IP novo". Esses IPs foram lidos do próprio AdsPower em 25/09/2026,
então já estão lá; marcá-los garante que o serviço do Mac não toque em nenhum
perfil existente e só aja no próximo IP que alguém digitar.

Revision ID: 0326_companies_ip_adspower
Revises: 0325_robo_margem_um_so
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0326_companies_ip_adspower"
down_revision: str | None = "0325_robo_margem_um_so"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.add_column("companies", sa.Column("ip_adspower", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column(
        "companies",
        sa.Column("ip_adspower_em", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "companies", sa.Column("ip_adspower_erro", sa.Text(), nullable=True), schema=SCHEMA
    )
    # Nada pendente no dia da publicação: o serviço só age em IP novo.
    op.execute(
        f"UPDATE {SCHEMA}.companies SET ip_adspower = ip, ip_adspower_em = now() "  # noqa: S608
        "WHERE ip IS NOT NULL AND btrim(ip) <> ''"
    )


def downgrade() -> None:
    op.drop_column("companies", "ip_adspower_erro", schema=SCHEMA)
    op.drop_column("companies", "ip_adspower_em", schema=SCHEMA)
    op.drop_column("companies", "ip_adspower", schema=SCHEMA)
