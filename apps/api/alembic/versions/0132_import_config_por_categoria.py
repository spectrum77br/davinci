"""import_config passa a ser por categoria (mala / eletro / celular)

Revision ID: 0132_import_config_por_categoria
Revises: 0131_user_password
Create Date: 2026-06-09

`import_config` era singleton (id=1) com tempo_reposicao/tempo_estoque
globais — o 120/60 da mala vazava pro celular. Espelha o padrão de
`import_cotacao_params` (já por categoria desde 0119):
  - id continua INTEGER PK (não rebatiza pra UUID — evita migrar dados
    e refs em routers; basta uma row por categoria).
  - ADD COLUMN `categoria VARCHAR(20)` UNIQUE + INDEX.
  - Carimba a linha existente (id=1) como categoria='mala'.
  - Cria a row de 'celular' com 150/60 (mesma fórmula que get_config
    auto-cria quando ausente).

Idempotente. Downgrade dropa coluna + a linha de celular.
"""

from alembic import op

revision = "0132_import_config_por_categoria"
down_revision = "0131_user_password"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_config "
        f"ADD COLUMN IF NOT EXISTS categoria VARCHAR(20)"
    )
    # Carimba a row existente (id=1, 120/60 ou outro valor do operador)
    # como 'mala' — mantém o estado atual antes do split.
    # noqa abaixo: schema e literais — sem input de usuário.
    op.execute(
        f"UPDATE {SCHEMA}.import_config SET categoria = 'mala' "  # noqa: S608
        f"WHERE id = 1 AND categoria IS NULL"
    )
    # NOT NULL após backfill.
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_config "
        f"ALTER COLUMN categoria SET NOT NULL"
    )
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS ix_import_config_categoria "
        f"ON {SCHEMA}.import_config (categoria)"
    )
    # Sincroniza a sequence com MAX(id) antes do INSERT do celular.
    # Era singleton com id=1 fixo (default=1 no model antigo), então em
    # prod a sequence ficou em last_value=1, is_called=false → o
    # próximo nextval devolveria 1, COLIDINDO com a PK do row da mala.
    # `ON CONFLICT (categoria)` só pega o índice de categoria — conflito
    # de PK não é coberto e a transação aborta. Sem isso, o INSERT
    # abaixo falha e a migration inteira é revertida.
    op.execute(
        f"SELECT setval('{SCHEMA}.import_config_id_seq', "  # noqa: S608
        f"(SELECT COALESCE(MAX(id), 1) FROM {SCHEMA}.import_config), true)"
    )
    # Semeia 'celular' com defaults (150/60). 'eletro' será auto-criado
    # pelo get_config no 1º acesso, mantém schema simples.
    op.execute(
        f"INSERT INTO {SCHEMA}.import_config (categoria, tempo_reposicao, tempo_estoque) "  # noqa: S608
        f"VALUES ('celular', 150, 60) "
        f"ON CONFLICT (categoria) DO NOTHING"
    )


def downgrade() -> None:
    # Apaga rows de não-mala pra preservar o singleton id=1 antes de
    # remover a coluna.
    op.execute(
        f"DELETE FROM {SCHEMA}.import_config WHERE categoria <> 'mala'"  # noqa: S608
    )
    op.execute(
        f"DROP INDEX IF EXISTS {SCHEMA}.ix_import_config_categoria"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_config DROP COLUMN IF EXISTS categoria"
    )
