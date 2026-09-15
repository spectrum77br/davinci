# ruff: noqa: E501, S608
"""vw_perfis: grupo Marrocos entra logo depois do Israel no desempate

Eduardo 15/09: "pode liberar o grupo Marrocos para o nosso sistema, e o nosso
agente de chamados". O grupo Marrocos (15 perfis, SunBrowser/Chrome como o
Israel) cobre as contas que não têm perfil no Israel — Forpaper e Marquezini no
Mercado Livre; Inova, KFA, Minas e Poofy na Shopee; KFA e Poofy na Amazon.

Na 0261 o desempate era "Israel primeiro, depois dedicado, depois número": conta
sem Israel caía no perfil da Contabilidade quando ele também era dedicado e tinha
número menor ("Forpaper - ml" 41 ganhava do "Forpaper - Mercado Livre" 74). Agora
a ordem é Israel, Marrocos e só então os demais grupos.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0280_vw_perfis_grupo_marrocos"
down_revision: str | None = "0279_financeiro_esteira_lenta"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

_CORPO = """
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
  FROM {s}.adspower_conta ac
  JOIN {s}.adspower a ON a.id = ac.adspower_id
  JOIN {s}.store_info si
    ON lower(si.platform) = ac.marketplace
   AND regexp_replace(lower(unaccent(si.account_name)), '[^a-z0-9]+', '', 'g') = ac.conta_key
 ORDER BY ac.marketplace, ac.conta_key,
          {ordem_grupo},
          ac.dedicado DESC,
          NULLIF(regexp_replace(a.profile_no, '[^0-9]', '', 'g'), '')::int NULLS LAST
"""

_ORDEM_NOVA = "CASE a.group_name WHEN 'Israel' THEN 0 WHEN 'Marrocos' THEN 1 ELSE 2 END"
_ORDEM_0261 = "(a.group_name = 'Israel') DESC"


def upgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.vw_perfis")
    op.execute(f"CREATE OR REPLACE VIEW {SCHEMA}.vw_perfis AS" + _CORPO.format(s=SCHEMA, ordem_grupo=_ORDEM_NOVA))


def downgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.vw_perfis")
    op.execute(f"CREATE OR REPLACE VIEW {SCHEMA}.vw_perfis AS" + _CORPO.format(s=SCHEMA, ordem_grupo=_ORDEM_0261))
