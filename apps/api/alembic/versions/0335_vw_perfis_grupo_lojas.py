# ruff: noqa: E501, S608
"""vw_perfis: grupo "Lojas" no lugar de Israel + Marrocos no desempate

Eduardo 28/09: "precisamos reorganizar os grupos, vamos fazer 3 grupos: Lojas
(todas as lojas), Contabilidade (que já está) e Administrativo". O "Israel" é
renomeado para "Lojas" e recebe os perfis do "Marrocos" (e os de loja que
estavam em Contas / Financeiro 2).

O desempate da view preferia 'Israel' e depois 'Marrocos' — é assim que a conta
cai no perfil dedicado ("KIA - Mercado Livre") e não no antigo da Contabilidade
("KIA - am ml sh she"). Só com o nome novo, 5 lojas passariam a abrir o perfil
antigo (simulado em produção). 'Lojas' entra com a mesma prioridade do Israel;
Israel e Marrocos continuam valendo até o espelho do AdsPower (adspower_sync)
trazer os nomes novos. Simulação com os grupos novos: 77 contas, 0 mudam de
perfil.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0335_vw_perfis_grupo_lojas"
down_revision: str | None = "0334_nf_caixa"
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

_ORDEM_NOVA = "CASE a.group_name WHEN 'Lojas' THEN 0 WHEN 'Israel' THEN 0 WHEN 'Marrocos' THEN 1 ELSE 2 END"
_ORDEM_0280 = "CASE a.group_name WHEN 'Israel' THEN 0 WHEN 'Marrocos' THEN 1 ELSE 2 END"


def upgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.vw_perfis")
    op.execute(f"CREATE OR REPLACE VIEW {SCHEMA}.vw_perfis AS" + _CORPO.format(s=SCHEMA, ordem_grupo=_ORDEM_NOVA))


def downgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.vw_perfis")
    op.execute(f"CREATE OR REPLACE VIEW {SCHEMA}.vw_perfis AS" + _CORPO.format(s=SCHEMA, ordem_grupo=_ORDEM_0280))
