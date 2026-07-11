"""marketing: Oferta Relâmpago — flash_duplicate_enabled por conta (contas de malas)

Liga a duplicação automática da Oferta Relâmpago (Shopee flash-sale). O cron
`marketing_flash_duplicate` roda 01:00 BRT e enfileira um comando 'flash_duplicate'
(executor='browser') por conta com `flash_duplicate_enabled=True`; o executor
local duplica a oferta 'Em andamento' pro próximo dia com aberturas.

- marketing_accounts.flash_duplicate_enabled: default false. Sobe já True nas 4
  contas "de malas" (Poofy, KFA, Minas, Inova), que foram as validadas pro fluxo.

Aditivo e reversível. Downgrade remove a coluna.
"""
from alembic import op

revision = "0176_marketing_flash_duplicate"
down_revision = "0175_marketing_adspower_executor"
branch_labels = None
depends_on = None

SCHEMA = "davinci"

# As contas "de malas" que rodam a duplicação diária da Oferta Relâmpago.
_MALAS = ("Poofy", "KFA", "Minas", "Inova")


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.marketing_accounts
            ADD COLUMN IF NOT EXISTS flash_duplicate_enabled BOOLEAN NOT NULL DEFAULT false
        """
    )
    # Liga nas contas de malas já existentes (idempotente: só sobe pra true).
    names = ", ".join("'" + n.replace("'", "''") + "'" for n in _MALAS)
    op.execute(
        f"""
        UPDATE {SCHEMA}.marketing_accounts
           SET flash_duplicate_enabled = true
         WHERE platform = 'shopee' AND name IN ({names})
        """
    )


def downgrade() -> None:
    op.execute(
        f"ALTER TABLE {SCHEMA}.marketing_accounts DROP COLUMN IF EXISTS flash_duplicate_enabled"
    )
