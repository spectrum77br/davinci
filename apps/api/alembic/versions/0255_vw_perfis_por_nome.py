# ruff: noqa: E501, S608
"""vw_perfis: casa o perfil AdsPower pelo NOME, não só pelo número final

Eduardo 10/09: o grupo novo "Israel" tem 54 perfis, um por conta de marketplace
("Aguiar - Mercado Livre", "Mega - Shopee"...). A `vw_perfis` só sabia casar
pelo número no FIM do nome (`VITA - ml sh ali.66` -> store_info.server 66), que
os perfis novos não têm — então nenhum deles aparecia e o robô de chamados não
conseguia abrir Shopee/Amazon/TikTok (10 casos de logística parados na Shopee,
2 na Amazon).

Agora a view é a UNIÃO de dois casamentos:

  1. POR NOME (perfis dedicados, grupo Israel): "<conta> - <marketplace>" —
     conta e marketplace normalizados (sem acento/espaço/maiúscula). Dois
     apelidos herdados: `victor` = "victor mei" e `zortex` = "zorvex".
     50 dos 54 casam direto; com os apelidos, 52. O "Locagil - Afliados sh ml
     te" fica de fora de propósito: é conta de afiliados, não loja.
  2. POR NÚMERO (perfis antigos, grupo Contabilidade): a regra original, onde
     UM perfil atende várias contas ("AGUIAR - ml | KFA - am shopp, shein.72").

`dedicado` diz de onde veio a linha; quem consome deve preferir o perfil
dedicado quando existir (é 1 conta por perfil, sem disputa de sessão).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0255_vw_perfis_por_nome"
down_revision: str | None = "0254_devolucao_pacote_entregue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
VIEW = "vw_perfis"

# minúsculas, sem acento, só letras e números
_NORM = "regexp_replace(lower(unaccent({expr})), '[^a-z0-9]+', '', 'g')"

_SQL_NOVA = f"""
CREATE OR REPLACE VIEW {SCHEMA}.{VIEW} AS
WITH perfil_nome AS (
    SELECT a.id, a.profile_no, a.name, a.group_name,
           btrim(split_part(a.name, ' - ', 1))                      AS conta_txt,
           btrim(substring(a.name from position(' - ' in a.name) + 3)) AS mkt_txt
    FROM {SCHEMA}.adspower a
    WHERE position(' - ' in a.name) > 0
      AND (regexp_match(a.name, '(\\d+)\\s*$'))[1] IS NULL  -- perfil dedicado não tem nº no fim
), perfil_norm AS (
    SELECT p.*,
           CASE {_NORM.format(expr="p.conta_txt")}
                WHEN 'victor' THEN 'victormei'
                WHEN 'zortex' THEN 'zorvex'
                ELSE {_NORM.format(expr="p.conta_txt")}
           END AS conta_key,
           CASE {_NORM.format(expr="p.mkt_txt")}
                WHEN 'mercadolivre' THEN 'ml'
                WHEN 'shopee'       THEN 'shopee'
                WHEN 'tiktok'       THEN 'tiktok'
                WHEN 'amazon'       THEN 'amazon'
                WHEN 'temu'         THEN 'temu'
                WHEN 'magalu'       THEN 'magalu'
                WHEN 'shein'        THEN 'shein'
                WHEN 'aliexpress'   THEN 'aliexpress'
                ELSE NULL
           END AS mkt_key
    FROM perfil_nome p
)
SELECT si.server AS perfil,
       pn.id           AS adspower_id,
       pn.profile_no   AS adspower_profile_no,
       pn.name         AS adspower_name,
       pn.group_name   AS adspower_group,
       true            AS dedicado,
       si.platform     AS marketplace,
       si.account_name AS conta,
       si.email,
       si.password_enc AS senha,
       si.id           AS store_info_id
  FROM {SCHEMA}.store_info si
  JOIN perfil_norm pn
    ON pn.mkt_key = {_NORM.format(expr="si.platform")}
   AND pn.conta_key = {_NORM.format(expr="si.account_name")}
UNION ALL
SELECT si.server AS perfil,
       a.id, a.profile_no, a.name, a.group_name,
       false AS dedicado,
       si.platform, si.account_name, si.email, si.password_enc, si.id
  FROM {SCHEMA}.store_info si
  JOIN {SCHEMA}.adspower a
    ON (regexp_match(a.name, '(\\d+)\\s*$'))[1] = si.server::text
"""

_SQL_ANTIGA = f"""
CREATE OR REPLACE VIEW {SCHEMA}.{VIEW} AS
SELECT si.server AS perfil,
       a.id AS adspower_id,
       a.profile_no AS adspower_profile_no,
       a.name AS adspower_name,
       a.group_name AS adspower_group,
       si.platform AS marketplace,
       si.account_name AS conta,
       si.email,
       si.password_enc AS senha,
       si.id AS store_info_id
  FROM {SCHEMA}.store_info si
  JOIN {SCHEMA}.adspower a
    ON (regexp_match(a.name, '(\\d+)\\s*$'))[1] = si.server::text
"""


def upgrade() -> None:
    # `unaccent` normaliza nomes de conta com acento antes de comparar.
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.{VIEW}")
    op.execute(_SQL_NOVA)


def downgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.{VIEW}")
    op.execute(_SQL_ANTIGA)
