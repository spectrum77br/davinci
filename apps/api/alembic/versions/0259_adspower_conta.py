# ruff: noqa: E501, S608
"""adspower_conta: vínculo perfil ↔ (conta, marketplace) resolvido no sincronizador

A `vw_perfis` casava perfil e loja por regex no NOME do perfil. Isso quebrou de
vez em 10/09: os perfis da Contabilidade foram renomeados e perderam o número
final (`FORPAPER - ml.74` → `Forpaper - ml`), então o casamento por número
deixou de achar qualquer coisa; e o grupo novo "Israel" (54 perfis, um por
conta) nunca teve número nenhum.

Os nomes hoje têm DOIS padrões, e ainda podem trazer mais de um bloco na mesma
string (ex.: "Nexus - am ml\nMinas - sh"):

    dedicado    "Aguiar - Mercado Livre"     → 1 conta, 1 marketplace
    compartilhado "KIA - am ml sh she"       → 1 conta, 4 marketplaces

Fazer isso em SQL vira regex ilegível. O `adspower_sync.py` passa a interpretar
o nome em Python e gravar aqui uma linha por (perfil, conta, marketplace);
a view só junta com store_info. `dedicado` = perfil exclusivo daquela conta e
marketplace — o robô deve preferir esses, porque não disputam sessão.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0259_adspower_conta"
down_revision: str | None = "0258_vw_perfis_por_nome"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

_VIEW = f"""
CREATE OR REPLACE VIEW {SCHEMA}.vw_perfis AS
SELECT si.server        AS perfil,
       a.id             AS adspower_id,
       a.profile_no     AS adspower_profile_no,
       a.name           AS adspower_name,
       a.group_name     AS adspower_group,
       ac.dedicado,
       si.platform      AS marketplace,
       si.account_name  AS conta,
       si.email,
       si.password_enc  AS senha,
       si.id            AS store_info_id
  FROM {SCHEMA}.adspower_conta ac
  JOIN {SCHEMA}.adspower a ON a.id = ac.adspower_id
  JOIN {SCHEMA}.store_info si
    ON lower(si.platform) = ac.marketplace
   AND regexp_replace(lower(unaccent(si.account_name)), '[^a-z0-9]+', '', 'g') = ac.conta_key
"""

_VIEW_ANTERIOR = f"""
CREATE OR REPLACE VIEW {SCHEMA}.vw_perfis AS
SELECT si.server AS perfil, a.id AS adspower_id, a.profile_no AS adspower_profile_no,
       a.name AS adspower_name, a.group_name AS adspower_group,
       si.platform AS marketplace, si.account_name AS conta, si.email,
       si.password_enc AS senha, si.id AS store_info_id
  FROM {SCHEMA}.store_info si
  JOIN {SCHEMA}.adspower a ON (regexp_match(a.name, '(\\d+)\\s*$'))[1] = si.server::text
"""


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.create_table(
        "adspower_conta",
        sa.Column("adspower_id", sa.Text(), nullable=False),
        sa.Column("marketplace", sa.Text(), nullable=False),
        sa.Column("conta_key", sa.Text(), nullable=False),
        sa.Column("conta_txt", sa.Text(), nullable=True),
        sa.Column("dedicado", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("adspower_id", "marketplace", "conta_key"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_adspower_conta_lookup", "adspower_conta", ["marketplace", "conta_key"], schema=SCHEMA
    )
    op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.vw_perfis")
    op.execute(_VIEW)


def downgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.vw_perfis")
    op.execute(_VIEW_ANTERIOR)
    op.drop_index("ix_adspower_conta_lookup", table_name="adspower_conta", schema=SCHEMA)
    op.drop_table("adspower_conta", schema=SCHEMA)
