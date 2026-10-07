"""Marketing: estado da coleta de Ads por conta + conta arquivada (sem apagar nada)

Conserto da coleta de Ads do Mercado Livre (07/10/2026, docs/marketing-ml-ads.md):

- marketing_accounts.sync_status / sync_erro / sync_em / sync_ok_em: o
  resultado da última coleta pela API ('ok' | 'erro' | 'sem_permissao'). Antes
  a coleta do ML marcava sucesso mesmo com o ML respondendo 404 e o gasto ficou
  zerado desde julho sem aviso.
- marketing_accounts.arquivada_em / arquivada_motivo: conta fora de todas as
  telas e consultas do Marketing e fora da coleta. Nada é apagado.

Dados: as contas da Amazon SEM integração são as 2 "Kfa" (celular/mala) que o
POST /api/marketing/seed de demonstração criou em 20/05/2026. O robô de
demonstração (services/marketing/agent.py, valores aleatórios) escreve ~190
linhas falsas por dia nelas. Aqui elas ficam arquivadas e com o robô
desligado (agent_enabled=false). As linhas de métricas/decisões/campanhas
delas CONTINUAM no banco (decisão do dono: desativar, não apagar).

- marketing_metrics: índice ÚNICO parcial (account_id, timestamp) WHERE
  intensity = 0 — uma linha DIÁRIA por conta e dia. A coleta do ML passou a
  criar todos os dias da janela (7 no cron, até 90 no backfill) e o cron
  (:05/:35) e o backfill podem rodar juntos: sem a chave, os dois inseriam o
  mesmo dia e as telas somavam o gasto em dobro. Em produção (07/10/2026):
  27.598 linhas, 2.563 diárias, 0 grupos repetidos — o índice entra limpo.
  Se aparecer repetido até o deploy, o CREATE falha com a chave repetida e a
  migration inteira volta (nada pela metade); ver a consulta em
  SQL_DIARIAS_REPETIDAS.

Só ADD COLUMN nulável (sem reescrever a tabela) + 1 UPDATE em 2 linhas +
1 índice (tabela pequena; trava de escrita de fração de segundo).

Revision ID: 0381_marketing_ads_estado
Revises: 0380_garantias
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0381_marketing_ads_estado"
down_revision: str | None = "0380_garantias"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
LOCK_TIMEOUT = "10s"

MOTIVO_DEMO = (
    "Conta de demonstração (criada pelo /seed em 20/05/2026, sem integração): "
    "os números dela eram inventados pelo robô de demonstração. Arquivada pela 0381."
)

# Só arquiva e desliga o robô — nenhum DELETE (testado em
# tests/test_ml_ads_sync.py::test_migration_0381_so_arquiva_contas_demo).
SQL_ARQUIVAR_DEMO = """
    UPDATE {schema}.marketing_accounts
       SET agent_enabled = false,
           arquivada_em = now(),
           arquivada_motivo = :motivo
     WHERE platform = 'amazon'
       AND integration_id IS NULL
       AND arquivada_em IS NULL
"""

# Índice da linha diária (igual ao __table_args__ de MarketingMetric).
INDICE_DIARIA = "uq_marketing_metrics_conta_dia"

# Conferência antes do deploy (deve voltar 0 linhas).
SQL_DIARIAS_REPETIDAS = """
    SELECT account_id, "timestamp", count(*)
      FROM {schema}.marketing_metrics
     WHERE intensity = 0
     GROUP BY 1, 2
    HAVING count(*) > 1
"""

_COLUNAS = (
    ("sync_status", sa.String(32)),
    ("sync_erro", sa.Text()),
    ("sync_em", sa.DateTime(timezone=True)),
    ("sync_ok_em", sa.DateTime(timezone=True)),
    ("arquivada_em", sa.DateTime(timezone=True)),
    ("arquivada_motivo", sa.Text()),
)


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    for nome, tipo in _COLUNAS:
        op.add_column(
            "marketing_accounts", sa.Column(nome, tipo, nullable=True), schema=SCHEMA
        )
    op.execute(
        sa.text(SQL_ARQUIVAR_DEMO.format(schema=SCHEMA)).bindparams(motivo=MOTIVO_DEMO)
    )
    op.create_index(
        INDICE_DIARIA,
        "marketing_metrics",
        ["account_id", "timestamp"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("intensity = 0"),
    )


def downgrade() -> None:
    op.drop_index(INDICE_DIARIA, table_name="marketing_metrics", schema=SCHEMA)
    # agent_enabled continua false: religar o robô de demonstração é decisão
    # manual (e ele só roda com MARKETING_AGENTE_SIMULADO=true).
    for nome, _tipo in reversed(_COLUNAS):
        op.drop_column("marketing_accounts", nome, schema=SCHEMA)
