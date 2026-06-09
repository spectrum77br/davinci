"""estoque_dia_finalizado — trava CONF. ESTOQUE de dias finalizados

Revision ID: 0134_estoque_dia_finalizado
Revises: 0133_refund_created_by
Create Date: 2026-06-09

Em /api/estoque/envios, o badge `conferencia_estoque` era recalculado
LIVE comparando `estoque_conferidos` (total histórico de checks pro
dia) com `total_produtos` (count current). Quando entrava produto novo
em uma tag, dias passados regrediam de "total" → "parcial". Não devia.

Esta tabela registra que o admin "fechou" o dia (deu o ✓ definitivo
em section='envio'). No router, dias presentes aqui são exibidos como
"total" independente do count atual de produtos.

Backfill: dias com QUALQUER user já com section='envio' + conferido=true
historicamente são carimbados aqui (preserva o estado visual atual).

Downgrade dropa a tabela (sem perda de dados em stock_checks).
"""
# ruff: noqa: S608

from alembic import op

revision = "0134_estoque_dia_finalizado"
down_revision = "0133_refund_created_by"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.estoque_dia_finalizado (
            data DATE PRIMARY KEY,
            finalizado_em TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # Backfill: carimba todo dia que já teve section='envio' + conferido=true
    # (admin tinha ticado). Usa MIN(created_at) como finalizado_em pra
    # refletir a 1ª marcação histórica. ON CONFLICT por idempotência.
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.estoque_dia_finalizado (data, finalizado_em)
        SELECT reference_date, MIN(created_at)
        FROM {SCHEMA}.stock_checks
        WHERE section = 'envio' AND conferido = true
        GROUP BY reference_date
        ON CONFLICT (data) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.estoque_dia_finalizado")
