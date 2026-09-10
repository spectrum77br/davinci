# ruff: noqa: E501, S608
"""vw_perfis: UMA linha por (marketplace, conta), preferindo o perfil dedicado

Com a `adspower_conta`, uma conta pode ter dois perfis: o dedicado do grupo
Israel ("Aguiar - Mercado Livre") e o compartilhado antigo da Contabilidade
("Aguiar - ml sh"). A view devolvia os dois, e quem consome faz LEFT JOIN sem
desempate — o monitor de casos manuais passaria a abrir (e registrar) o MESMO
chamado duas vezes.

Agora a view escolhe: dedicado primeiro; empatando, o perfil de número menor
(os antigos, mais estáveis). Quem precisar do alternativo tem a
`adspower_conta` crua.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0260_vw_perfis_um_por_conta"
down_revision: str | None = "0259_adspower_conta"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

_UM_POR_CONTA = f"""
CREATE OR REPLACE VIEW {SCHEMA}.vw_perfis AS
SELECT DISTINCT ON (ac.marketplace, ac.conta_key)
       si.server        AS perfil,
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
 ORDER BY ac.marketplace, ac.conta_key, ac.dedicado DESC,
          NULLIF(regexp_replace(a.profile_no, '[^0-9]', '', 'g'), '')::int NULLS LAST
"""

_TODAS = f"""
CREATE OR REPLACE VIEW {SCHEMA}.vw_perfis AS
SELECT si.server AS perfil, a.id AS adspower_id, a.profile_no AS adspower_profile_no,
       a.name AS adspower_name, a.group_name AS adspower_group, ac.dedicado,
       si.platform AS marketplace, si.account_name AS conta, si.email,
       si.password_enc AS senha, si.id AS store_info_id
  FROM {SCHEMA}.adspower_conta ac
  JOIN {SCHEMA}.adspower a ON a.id = ac.adspower_id
  JOIN {SCHEMA}.store_info si
    ON lower(si.platform) = ac.marketplace
   AND regexp_replace(lower(unaccent(si.account_name)), '[^a-z0-9]+', '', 'g') = ac.conta_key
"""


def upgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.vw_perfis")
    op.execute(_UM_POR_CONTA)


def downgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.vw_perfis")
    op.execute(_TODAS)
