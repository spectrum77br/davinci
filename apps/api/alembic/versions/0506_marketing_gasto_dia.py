"""Histórico diário do gasto de Ads por conta (Investimento do Faturamento)

Pedido M-22 do dono (prévia aprovada em 23/11/2026): a aba Faturamento da
Tabela de Preços ganha a coluna Investimento = gasto de Ads + afiliados da
conta no PERÍODO filtrado. O Marketing só guardava `marketing_accounts.
spend_today`, sobrescrito a cada coleta.

ESTRUTURA (só aditiva):
- Tabela `marketing_gasto_dia` (marketing_account_id → marketing_accounts
  CASCADE, dia DATE) — chave primária (conta, dia): uma linha por dia.
- `gasto_ads` NUMERIC(12,2): gravado (upsert) por toda coleta que escreve o
  `spend_today` (ML: os 7 dias da janela; Shopee: os dias da resposta).
- `gasto_afiliados` NUMERIC(12,2): preparado, SEM FONTE hoje (fica NULL).
- `origem` ('ml_ads' | 'shopee_ads' | 'marketing_metrics') e `atualizado_em`.
- CHECKs: os dois gastos >= 0.

CÓPIA (upgrade): a coleta do ML já guardava uma linha diária por conta no
`marketing_metrics` (intensity = 0, 12:00 UTC; desde 20/05/2026) — o mesmo
número que a coleta passa a gravar aqui. Ela é copiada com origem
'marketing_metrics', para o Investimento ter o histórico desde já. Só ML e
Shopee de conta não arquivada: as linhas diárias da Amazon são a soma da
janela de 7 dias (não o gasto do dia) e as contas arquivadas são as de
demonstração.

Só de 09/09/2026 em diante (revisão de 24/11): antes disso o número do
`marketing_metrics` está errado e nunca foi consertado — inflado até 09/07 e
gasto 0 de 09/07 a 08/09 (a coleta recebia 404, gravava 0 e marcava sucesso;
conserto 5a007892, 08/10/2026). O backfill do conserto regravou de 09/09 a
08/10 (produção, 08/10 10h UTC) e a coleta consertada cuida de 08/10 em
diante; cada dia a partir de 09/09 foi regravado ou conferido igual por ela.
Copiar o antes faria agosto aparecer como R$ 0,00 e maio/junho inflados — sem
linha, a tela mostra "—" ou o asterisco de parcial.

DOWNGRADE: apaga a tabela (o `marketing_metrics` não é tocado).

`SET LOCAL lock_timeout` como na 0505: a FK nova trava `marketing_accounts`
(a coleta do Ads grava nela a cada 30 min); sem limite, uma transação longa
segurando a tabela deixaria a migration na fila e a coleta parada atrás dela.
Com o limite, a migration desiste em 5 s e é só rodar de novo.

Revision ID: 0506_marketing_gasto_dia
Revises: 0505_alertas_por_equipe
"""

# ruff: noqa: S608 — SQL montado só com nomes fixos desta migration (schema), sem entrada de fora
from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0506_marketing_gasto_dia"
down_revision: str | None = "0505_alertas_por_equipe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Constante de módulo: o teste troca pelo schema de teste.
SCHEMA = "davinci"

TABELA = "marketing_gasto_dia"
# FIXOS (não importam o modelo vivo).
ORIGEM_COPIA = "marketing_metrics"
PLATAFORMAS_COPIADAS = ("ml", "shopee")
# O 1º dia que a coleta consertada (5a007892) regravou ou conferiu (docstring).
PRIMEIRO_DIA_CONFIAVEL = date(2026, 9, 9)


def copiar_linhas_diarias(bind) -> None:
    """As linhas diárias que o Marketing já tinha (ver docstring do módulo)."""
    s = SCHEMA
    plataformas = ", ".join(f"'{p}'" for p in PLATAFORMAS_COPIADAS)
    bind.execute(
        sa.text(
            f"""
            INSERT INTO {s}.{TABELA} (marketing_account_id, dia, gasto_ads, origem)
            SELECT m.account_id,
                   (m.timestamp AT TIME ZONE 'UTC')::date,
                   round(max(m.spend)::numeric, 2),
                   :origem
              FROM {s}.marketing_metrics m
              JOIN {s}.marketing_accounts a ON a.id = m.account_id
             WHERE m.intensity = 0
               AND m.spend >= 0 AND m.spend < 1e10  -- fora NaN e infinito
               AND a.arquivada_em IS NULL
               AND a.platform IN ({plataformas})
               AND (m.timestamp AT TIME ZONE 'UTC')::date >= :desde
             GROUP BY m.account_id, (m.timestamp AT TIME ZONE 'UTC')::date
            ON CONFLICT (marketing_account_id, dia) DO NOTHING
            """
        ),
        {"origem": ORIGEM_COPIA, "desde": PRIMEIRO_DIA_CONFIAVEL},
    )


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.create_table(
        TABELA,
        sa.Column(
            "marketing_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.marketing_accounts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("dia", sa.Date(), primary_key=True),
        sa.Column("gasto_ads", sa.Numeric(12, 2), nullable=True),
        sa.Column("gasto_afiliados", sa.Numeric(12, 2), nullable=True),
        sa.Column("origem", sa.Text(), nullable=False),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Nome final (`op.f`), o mesmo que a convenção ck_<tabela>_<nome> dá ao modelo.
        sa.CheckConstraint("gasto_ads >= 0", name=op.f(f"ck_{TABELA}_gasto_ads")),
        sa.CheckConstraint("gasto_afiliados >= 0", name=op.f(f"ck_{TABELA}_gasto_afiliados")),
        schema=SCHEMA,
    )
    copiar_linhas_diarias(op.get_bind())


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")  # ver o upgrade
    op.drop_table(TABELA, schema=SCHEMA)
