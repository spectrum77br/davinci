"""marketing: a conta de Ads ligada à conta da Conferência (Horários dos anúncios)

Pedido do dono (13/10/2026): em Marketing › Conferência › (Shopee | Mercado
Livre) › Horários dos anúncios, o seletor "Conta" vira os botões Mala |
Celular | Eletro e a lista das contas do segmento (na Shopee, com o saldo de
Ads de cada uma). O segmento de cada conta de Ads sai da conta da Conferência
(`conferencia_shopee_conta.grupo`): o `marketing_accounts.department` está
'geral' nas 18 contas da Shopee e errado no kfa do ML ('celular'; na
Conferência é mala).

  • `marketing_accounts.conferencia_conta_id` — uuid NULL, FK →
    `conferencia_shopee_conta(id)` ON DELETE SET NULL (conta apagada da lista
    da Conferência: a de Ads fica sem vínculo e a tela acha de novo pelo nome
    ou pela integração);
  • `uq_marketing_accounts_conferencia_conta_id` — único PARCIAL (só os não
    nulos): uma conta da Conferência com UMA conta de Ads no máximo (duas
    mostrariam o mesmo saldo e o mesmo eletro);
  • semente (só onde está NULL, só conta de Ads não arquivada, idempotente):
      - ML: a conta da Conferência do ML com a MESMA `integration_id`;
      - Shopee: o nome normalizado (minúsculo, sem acento, só letras e
        números: " Lucas MEI" = "lucasmei") igual ao nome da conta da
        Conferência da Shopee; os apelidos JLAS → Atlas e KIA → Fiore (a
        Conferência mostra o nome da loja) valem só quando não há conta da
        Conferência com o próprio nome;
      - par que não é um-para-um (duas contas de Ads na mesma da Conferência,
        ou o contrário) e conta da Conferência que já tem dono ficam de fora.
    O `department` NÃO é tocado.

Só ADD COLUMN nulável (sem reescrever a tabela; dezenas de linhas) + índice
pequeno + FK. `lock_timeout` de 10 s (a FK pega ShareRowExclusiveLock nas duas
tabelas). O downgrade tira o índice, a FK e a coluna (o vínculo se perde; a
tela continua achando pelas regras da semente).

As mesmas regras moram em `services/marketing/horarios_contas.py` (conta
criada depois da migration). `tests/test_marketing_horarios_migration.py`
roda a migration num schema descartável, compara o catálogo com o model,
semeia com contas inventadas e desfaz.

Revision ID: 0392_marketing_conta_conferencia
Revises: 0391_email_fora_do_chat
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0392_marketing_conta_conferencia"
down_revision: str | None = "0391_email_fora_do_chat"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
LOCK_TIMEOUT = "10s"

TABELA = "marketing_accounts"
COLUNA = "conferencia_conta_id"
CONTA = "conferencia_shopee_conta"
FK = "fk_marketing_accounts_conferencia_conta"
INDICE = "uq_marketing_accounts_conferencia_conta_id"

# Apelido da conta de Ads (normalizado) → nome da conta na Conferência
# (normalizado). Os mesmos de services/marketing/horarios_contas.APELIDOS.
APELIDOS = {"jlas": "atlas", "kia": "fiore"}

# Minúsculo, sem acento, só letras e números. O mesmo de
# horarios_contas.chave_nome para os nomes de hoje (todos ASCII).
_NORMAL = (
    "regexp_replace(translate(lower({col}), "
    "'áàâãäåéèêëíìîïóòôõöúùûüçñ', 'aaaaaaeeeeiiiiooooouuuucn'), '[^a-z0-9]', '', 'g')"
)


def sql_semente(schema: str) -> str:
    """O UPDATE da semente (só onde está NULL; idempotente)."""
    s = f'"{schema}"'
    apelidos = ", ".join(f"('{de}', '{para}')" for de, para in APELIDOS.items())
    nome_ads = _NORMAL.format(col="m.name")
    nome_conf = _NORMAL.format(col="c.nome")
    return f"""
WITH apelido(de, para) AS (VALUES {apelidos}),
ads AS (
  SELECT m.id, m.platform, m.integration_id, {nome_ads} AS chave
    FROM {s}.{TABELA} m
   WHERE m.{COLUNA} IS NULL
     AND m.arquivada_em IS NULL
     AND m.platform IN ('shopee', 'ml')
),
conf AS (
  SELECT c.id, c.plataforma, c.integration_id, {nome_conf} AS chave
    FROM {s}.{CONTA} c
   WHERE c.plataforma IN ('shopee', 'ml')
),
livre AS (
  SELECT conf.* FROM conf
   WHERE NOT EXISTS (SELECT 1 FROM {s}.{TABELA} x WHERE x.{COLUNA} = conf.id)
),
par AS (
  SELECT a.id AS ads_id, l.id AS conta_id
    FROM ads a JOIN livre l
      ON a.platform = 'ml' AND l.plataforma = 'ml' AND l.integration_id = a.integration_id
  UNION ALL
  SELECT a.id, l.id
    FROM ads a JOIN livre l
      ON a.platform = 'shopee' AND l.plataforma = 'shopee' AND a.chave <> ''
     AND l.chave = a.chave
  UNION ALL
  SELECT a.id, l.id
    FROM ads a
    JOIN apelido ap ON ap.de = a.chave
    JOIN livre l ON a.platform = 'shopee' AND l.plataforma = 'shopee' AND l.chave = ap.para
   WHERE NOT EXISTS (
     SELECT 1 FROM conf c2 WHERE c2.plataforma = 'shopee' AND c2.chave = a.chave
   )
),
unico AS (
  SELECT p.ads_id, p.conta_id FROM par p
   WHERE (SELECT count(*) FROM par q WHERE q.ads_id = p.ads_id) = 1
     AND (SELECT count(*) FROM par q WHERE q.conta_id = p.conta_id) = 1
)
UPDATE {s}.{TABELA} m
   SET {COLUNA} = u.conta_id
  FROM unico u
 WHERE m.id = u.ads_id
   AND m.{COLUNA} IS NULL
"""  # noqa: S608


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.add_column(
        TABELA, sa.Column(COLUNA, pg.UUID(as_uuid=True), nullable=True), schema=SCHEMA
    )
    op.create_foreign_key(
        op.f(FK),
        TABELA,
        CONTA,
        [COLUNA],
        ["id"],
        ondelete="SET NULL",
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )
    op.create_index(
        INDICE,
        TABELA,
        [COLUNA],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text(f"{COLUNA} IS NOT NULL"),
    )
    op.execute(sql_semente(SCHEMA))


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.drop_index(INDICE, table_name=TABELA, schema=SCHEMA)
    op.drop_constraint(op.f(FK), TABELA, type_="foreignkey", schema=SCHEMA)
    op.drop_column(TABELA, COLUNA, schema=SCHEMA)
