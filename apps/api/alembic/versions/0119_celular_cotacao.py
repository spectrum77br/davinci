"""cotacao celular: params globais + valor_usd/realizado/frete_type por produto

Revision ID: 0119_celular_cotacao
Revises: 0118_margem_audit_generaliza
Create Date: 2026-06-02

Etapa 3 de 4 — habilita aba Cotação do Celular.

A aba calcula custo previsto em R$ a partir de:
  previsto_brl = valor_usd * (1 + frete_pct) * taxa_cambio + adicional

`frete_pct` depende do tipo de frete (regular/swap/acessorios), os
percentuais e a taxa de câmbio + adicional ficam em parâmetros
globais por categoria (extensível pra mala/eletro futuramente; só
celular tem seed inicial agora).

Schema:
  1. import_cotacao_params (categoria UNIQUE) — parâmetros editáveis
     no topo da aba. Seed pra celular com defaults validados pelo
     operador (16% regular / 6% swap / 20% acessórios / câmbio 5.10
     / adicional R$ 12).
  2. import_products + (valor_usd, valor_brl_realizado, frete_type) —
     o operador preenche por produto. `valor_brl_previsto` NÃO é
     persistido — calcula em tempo real no frontend pra não duplicar.

Idempotente: CREATE TABLE IF NOT EXISTS + ADD COLUMN IF NOT EXISTS +
ON CONFLICT no seed. Downgrade limpa tudo (DROP COLUMN e DROP TABLE
em ordem segura).
"""

from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision = "0119_celular_cotacao"
down_revision = "0118_margem_audit_generaliza"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    # 1) Tabela de parâmetros globais por categoria.
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.import_cotacao_params (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            categoria VARCHAR(20) NOT NULL UNIQUE,
            taxa_cambio NUMERIC(8,4) NOT NULL DEFAULT 5.10,
            frete_regular_pct NUMERIC(6,4) NOT NULL DEFAULT 0.16,
            frete_swap_pct NUMERIC(6,4) NOT NULL DEFAULT 0.06,
            frete_acessorios_pct NUMERIC(6,4) NOT NULL DEFAULT 0.20,
            adicional NUMERIC(10,2) NOT NULL DEFAULT 12.00,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    # 2) Estende import_products com os 3 novos campos. ADD COLUMN IF
    # NOT EXISTS pra idempotência (Postgres 9.6+).
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_products "
        f"ADD COLUMN IF NOT EXISTS valor_usd NUMERIC(10,2)"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_products "
        f"ADD COLUMN IF NOT EXISTS valor_brl_realizado NUMERIC(10,2)"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_products "
        f"ADD COLUMN IF NOT EXISTS frete_type VARCHAR(20) DEFAULT 'regular' "
        f"CHECK (frete_type IN ('regular','swap','acessorios'))"
    )

    # 3) Seed dos params pra celular (defaults validados pelo operador).
    conn = op.get_bind()
    conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.import_cotacao_params
                (categoria, taxa_cambio, frete_regular_pct,
                 frete_swap_pct, frete_acessorios_pct, adicional)
            VALUES ('celular', 5.10, 0.16, 0.06, 0.20, 12.00)
            ON CONFLICT (categoria) DO NOTHING
            """  # noqa: S608
        )
    )


def downgrade() -> None:
    # Remove colunas + tabela em ordem segura.
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_products DROP COLUMN IF EXISTS frete_type"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_products DROP COLUMN IF EXISTS valor_brl_realizado"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_products DROP COLUMN IF EXISTS valor_usd"
    )
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.import_cotacao_params")
