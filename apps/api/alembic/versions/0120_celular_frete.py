"""celular frete: transportadora/obs em lotes, pago em items, transp em resumo

Revision ID: 0120_celular_frete
Revises: 0119_celular_cotacao
Create Date: 2026-06-02

Etapa 4 de 4 (FINAL) — habilita aba Frete + completa Resumo do Celular.

Schema:
  1. import_lotes + (transportadora, obs)
       transportadora: agrupa lotes na aba Frete pelo dropdown
       obs: editável na aba Resumo (PATCH /lotes/{id})
  2. import_lote_items + pago
       Checkbox na aba Frete. Saldo só é cobrado de items NÃO pagos.
  3. import_resumo + transportadora
       Permite criar "ajuste manual" de frete via POST /lote_ajuste
       reusando ImportResumo (lote_id=NULL, transportadora=<X>, saldo=Y).
       A linha aparece na aba Frete (junto com items dos lotes) E na
       aba Resumo (lançamento avulso).

`Valor unit` e `frete %` da aba Frete NÃO entram no schema: vêm
respectivamente de ImportProduct.valor_brl_realizado (etapa 3) e do
mapa ImportProduct.frete_type → ImportCotacaoParams.frete_*_pct.
Fonte única de verdade — operador edita na aba Cotação.

Idempotente: ADD COLUMN IF NOT EXISTS.
Downgrade reverso.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0120_celular_frete"
down_revision = "0119_celular_cotacao"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_lotes "
        f"ADD COLUMN IF NOT EXISTS transportadora VARCHAR(100)"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_lotes "
        f"ADD COLUMN IF NOT EXISTS obs TEXT"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_lote_items "
        f"ADD COLUMN IF NOT EXISTS pago BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_resumo "
        f"ADD COLUMN IF NOT EXISTS transportadora VARCHAR(100)"
    )


def downgrade() -> None:
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_resumo DROP COLUMN IF EXISTS transportadora"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_lote_items DROP COLUMN IF EXISTS pago"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_lotes DROP COLUMN IF EXISTS obs"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_lotes DROP COLUMN IF EXISTS transportadora"
    )
