"""import: coluna categoria (mala/eletro/celular) nas tabelas import_*

Adiciona `categoria` (default 'mala' pra não quebrar dados existentes) +
index + CHECK constraint nas 8 tabelas que o selector top-level filtra.
import_config (singleton), import_lote_items e cotacao_valores NÃO
recebem — o contexto vem da FK pro pai.

Revision ID: 0106_import_categoria
Revises: 0105_devolution_tag_data_estoque
Create Date: 2026-05-28
"""

from alembic import op

revision = "0106_import_categoria"
down_revision = "0105_devolution_tag_data_estoque"
branch_labels = None
depends_on = None

_TABLES = (
    "import_products",
    "import_lotes",
    "import_resumo",
    "cotacao_fabricantes",
    "cotacao_produtos",
    "import_kit_variations",
    "import_kit_bases",
    "import_kit_marks",
)


def upgrade() -> None:
    # Idempotente (IF NOT EXISTS / guard) — a coluna pode já ter sido
    # aplicada manualmente durante a reconciliação de um desencontro de
    # alembic em prod; nesse caso o upgrade no-opa em vez de quebrar.
    for t in _TABLES:
        op.execute(
            f'ALTER TABLE {t} ADD COLUMN IF NOT EXISTS '
            f"categoria varchar(20) NOT NULL DEFAULT 'mala'"
        )
        op.execute(f'CREATE INDEX IF NOT EXISTS ix_{t}_categoria ON {t} (categoria)')
        op.execute(
            "DO $$ BEGIN "
            f"IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_{t}_categoria') THEN "
            f"ALTER TABLE {t} ADD CONSTRAINT ck_{t}_categoria "
            "CHECK (categoria IN ('mala','eletro','celular')); "
            "END IF; END $$;"
        )


def downgrade() -> None:
    for t in _TABLES:
        op.execute(f'ALTER TABLE {t} DROP CONSTRAINT IF EXISTS ck_{t}_categoria')
        op.execute(f'DROP INDEX IF EXISTS ix_{t}_categoria')
        op.execute(f'ALTER TABLE {t} DROP COLUMN IF EXISTS categoria')
