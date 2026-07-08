"""marketing: executor externo (AdsPower) — adspower_user_id, applied_state, executor, heartbeat

Fase 1 da integração com o executor local "marionete". O DaVinci vira o plano
de controle (UI + agenda BRT + outbox marketing_commands), mas a execução na
Shopee acontece numa máquina LOCAL via AdsPower (navegador), porque (a) a API
oficial de Ads da Shopee está bloqueada pela cota e (b) a Oferta Relâmpago não
tem API. O executor local roda atrás de NAT, então faz POLL de saída na API do
DaVinci (/marketing/agent/*) em vez de o DaVinci alcançá-lo.

- marketing_accounts.adspower_user_id: id do perfil AdsPower que o executor
  abre pra dirigir esta loja no navegador. NULL em ML/Amazon (usam API oficial).
- marketing_accounts.applied_state ('on'|'off'): último estado aplicado pelo
  executor externo. Contas 'browser' NÃO têm campanhas sincronizadas (o sync usa
  a API bloqueada), então o reconciler compara o estado desejado contra este
  campo em vez das campanhas.
- marketing_commands.executor ('api'|'browser'): canal de execução. 'api' =
  consumidor interno (ML/Amazon). 'browser' = executor externo (Shopee via
  AdsPower); é entregue por /marketing/agent/lease e NUNCA passa pelo consumidor
  interno.
- marketing_agent_heartbeat: presença do executor local (UI mostra online/offline).

Tudo aditivo e reversível. Downgrade remove as colunas, o índice e a tabela.
"""
from alembic import op

revision = "0175_marketing_adspower_executor"
down_revision = "0174_pricing_account_note_fields"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.marketing_accounts
            ADD COLUMN IF NOT EXISTS adspower_user_id VARCHAR(64),
            ADD COLUMN IF NOT EXISTS applied_state VARCHAR(8)
        """
    )
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.marketing_commands
            ADD COLUMN IF NOT EXISTS executor VARCHAR(16) NOT NULL DEFAULT 'api'
        """
    )
    # Índice parcial pro lease do executor externo: só comandos 'browser'
    # pendentes, ordenados por chegada (o claim usa status + created_at).
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS ix_marketing_commands_browser_pending
        ON {SCHEMA}.marketing_commands (status, created_at)
        WHERE executor = 'browser'
        """
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.marketing_agent_heartbeat (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            agent_name VARCHAR(64) NOT NULL UNIQUE,
            last_seen_at TIMESTAMPTZ,
            adspower_ok BOOLEAN,
            accounts_online INTEGER,
            info JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.marketing_agent_heartbeat")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_marketing_commands_browser_pending")
    op.execute(
        f"ALTER TABLE {SCHEMA}.marketing_commands DROP COLUMN IF EXISTS executor"
    )
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.marketing_accounts
            DROP COLUMN IF EXISTS applied_state,
            DROP COLUMN IF EXISTS adspower_user_id
        """
    )
