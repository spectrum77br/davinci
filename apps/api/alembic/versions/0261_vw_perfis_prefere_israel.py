# ruff: noqa: E501, S608
"""vw_perfis: desempate prefere o grupo dedicado (Israel), não o número menor

Eduardo 10/09, ao ver o robô abrir a conta velasco: "você abriu perfil 49, é da
contabilidade". Estava certo. A conta tem DOIS perfis marcados como dedicados —
"Velasco - ml" (49, Contabilidade) e "Velasco - Mercado Livre" (81, Israel) —
porque ambos servem 1 conta e 1 marketplace; o desempate por profile_no menor
escolhia o antigo.

O grupo Israel é o que o Eduardo montou com um perfil por conta e mantém logado,
e roda SunBrowser (Chrome/CDP). Os da Contabilidade são FlowerBrowser (Firefox/
Marionette) e alguns nem abrem com o driver do Chrome — foi o que aconteceu com
o 41 (Forpaper) e o 49 (Velasco) na varredura. Então a ordem passa a ser:
grupo dedicado primeiro, depois `dedicado`, depois o número.

Contas que só existem na Contabilidade (Forpaper, Marquezini, Poofy, Minas,
Luno) continuam caindo lá — e por isso o robô precisa dos dois drivers.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0261_vw_perfis_prefere_israel"
down_revision: str | None = "0260_vw_perfis_um_por_conta"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
# Grupo do AdsPower com 1 perfil por conta/marketplace (Chrome, sessão viva).
GRUPO_DEDICADO = "Israel"

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
          (a.group_name = '{g}') DESC,
          ac.dedicado DESC,
          NULLIF(regexp_replace(a.profile_no, '[^0-9]', '', 'g'), '')::int NULLS LAST
"""


def upgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.vw_perfis")
    op.execute(
        f"CREATE OR REPLACE VIEW {SCHEMA}.vw_perfis AS"
        + _CORPO.format(s=SCHEMA, g=GRUPO_DEDICADO)
    )


def downgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.vw_perfis")
    # volta ao desempate só por dedicado + número
    op.execute(
        f"CREATE OR REPLACE VIEW {SCHEMA}.vw_perfis AS"
        + _CORPO.format(s=SCHEMA, g="__nenhum__")
    )
