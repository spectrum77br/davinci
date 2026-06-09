"""audit_em_andamento_data — câmera pra rastrear quem re-carimba

Revision ID: 0135_audit_em_andamento_data
Revises: 0134_estoque_dia_finalizado
Create Date: 2026-06-09

Apesar dos fixes (b8d2f5a COALESCE, trigger protect_em_andamento_data
que impede UPDATE→NULL), `em_andamento_data` continua sendo
re-carimbado em prod. Operador conserta manualmente todo dia. Esta
migration é só a CÂMERA — observa por 24h+ pra identificar o serviço/
job culpado, depois mata o caminho residual em outro commit.

Captura:
- application_name (passado pelo asyncpg via server_settings) — bate
  com o env var APP_NAME de cada container (api / worker / worker_ui
  / worker_marketplace).
- session_user + pg_backend_pid pra cruzar com pg_stat_activity.
- old vs new (data E situacao) → identifica padrão de re-carimbo.

Downgrade dropa trigger + função + tabela (sem perda de dados em
bling_orders).
"""
# ruff: noqa: S608

from alembic import op

revision = "0135_audit_em_andamento_data"
down_revision = "0134_estoque_dia_finalizado"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.audit_em_andamento_data (
            id BIGSERIAL PRIMARY KEY,
            bling_order_id UUID NOT NULL,
            bling_id BIGINT,
            numero TEXT,
            op CHAR(1) NOT NULL,
            old_data DATE,
            new_data DATE,
            old_situacao TEXT,
            new_situacao TEXT,
            application_name TEXT,
            -- `session_user` é palavra reservada do SQL (função builtin),
            -- precisa quotar quando usada como nome de coluna.
            "session_user" TEXT NOT NULL DEFAULT current_user,
            pg_backend_pid INTEGER NOT NULL DEFAULT pg_backend_pid(),
            changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS ix_audit_em_andamento_changed_at
        ON {SCHEMA}.audit_em_andamento_data (changed_at DESC)
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS ix_audit_em_andamento_order
        ON {SCHEMA}.audit_em_andamento_data (bling_order_id)
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.audit_em_andamento_data_fn()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.em_andamento_data IS NOT NULL THEN
                    INSERT INTO {SCHEMA}.audit_em_andamento_data
                        (bling_order_id, bling_id, numero, op,
                         old_data, new_data, old_situacao, new_situacao,
                         application_name)
                    VALUES
                        (NEW.id, NEW.bling_id, NEW.numero, 'I',
                         NULL, NEW.em_andamento_data, NULL, NEW.situacao,
                         current_setting('application_name', true));
                END IF;
            ELSIF TG_OP = 'UPDATE'
                  AND OLD.em_andamento_data IS DISTINCT FROM NEW.em_andamento_data THEN
                INSERT INTO {SCHEMA}.audit_em_andamento_data
                    (bling_order_id, bling_id, numero, op,
                     old_data, new_data, old_situacao, new_situacao,
                     application_name)
                VALUES
                    (NEW.id, NEW.bling_id, NEW.numero, 'U',
                     OLD.em_andamento_data, NEW.em_andamento_data,
                     OLD.situacao, NEW.situacao,
                     current_setting('application_name', true));
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        f"""
        DROP TRIGGER IF EXISTS bling_orders_audit_em_andamento_data
        ON {SCHEMA}.bling_orders
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER bling_orders_audit_em_andamento_data
        AFTER INSERT OR UPDATE OF em_andamento_data
        ON {SCHEMA}.bling_orders
        FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.audit_em_andamento_data_fn()
        """
    )


def downgrade() -> None:
    op.execute(
        f"DROP TRIGGER IF EXISTS bling_orders_audit_em_andamento_data "
        f"ON {SCHEMA}.bling_orders"
    )
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.audit_em_andamento_data_fn()")
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.audit_em_andamento_data")
