# ruff: noqa: E501, S608
"""vw_perfis: agrupa os perfis AdsPower com as lojas (store_info)

Liga cada loja ao seu perfil AdsPower. O numero do "Servidor" exibido na
pagina Lojas (`store_info.server`) corresponde ao numero no FINAL do
`adspower.name` (ex.: `VITA - ml sh ali.66` -> servidor 66). Como nao ha FK,
o match e feito extraindo esse numero final do nome do perfil.

Cada linha = uma conta/marketplace da loja, enriquecida com o id do perfil
AdsPower. INNER JOIN: lojas sem perfil AdsPower correspondente ficam de fora.

A senha (`store_info.password_enc`) e exposta CIFRADA (AES-GCM) -- a
descriptografia ocorre na API via app.security.cipher.decrypt().

Revision ID: 0108_vw_perfis
Revises: 0107_normalize_devolution_tags
Create Date: 2026-05-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0108_vw_perfis"
down_revision: str | None = "0107_normalize_devolution_tags"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
VIEW = "vw_perfis"

_VIEW_SQL = rf"""
CREATE OR REPLACE VIEW {SCHEMA}.{VIEW} AS
SELECT
    si.server                          AS perfil,
    a.id                               AS adspower_id,
    a.profile_no                       AS adspower_profile_no,
    a.name                             AS adspower_name,
    a.group_name                       AS adspower_group,
    si.platform                        AS marketplace,
    si.account_name                    AS conta,
    si.email,
    si.password_enc                    AS senha,
    si.id                              AS store_info_id
FROM {SCHEMA}.store_info si
JOIN {SCHEMA}.adspower a
  ON (regexp_match(a.name, '(\d+)\s*$'))[1] = si.server
""".strip()


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(f'DROP VIEW IF EXISTS "{SCHEMA}"."{VIEW}" CASCADE')
    op.execute(_VIEW_SQL)


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(f'DROP VIEW IF EXISTS "{SCHEMA}"."{VIEW}" CASCADE')
