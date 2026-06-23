"""bling_envio_evento — ledger de envios por EVENTO (entrada em situação 15)

A aba Envios contava `COUNT(DISTINCT bling_id)` agrupando por
`bling_orders.em_andamento_data` — um DATE mutável, sem hora. Três problemas:

  1. Sem corte de 08:00. O dono quer que tudo que entrou em "em andamento"
     entre 08:00 de hoje e 07:59 de amanhã conte como envio de HOJE. Um DATE
     não expressa esse corte.
  2. Recarimbo. `em_andamento_data` é reescrito pelo sync diário (DELETE+INSERT,
     ~05h BRT — ver 0150) e por UPDATEs manuais de psql. Pedidos "pulam de dia".
  3. Conta estado atual, não evento. Se o pedido depois sai do conjunto verde
     (cancelado/sucata) some retroativamente da contagem.

Esta migration registra o EVENTO "pedido entrou na situação 15" numa tabela
append-only (ledger), com `occurred_at` imutável e um `shipping_day` ancorado
nas 08:00 BRT. A captura é por TRIGGER de banco (mesmo padrão de 0150) porque o
sync faz DELETE+INSERT cru e UPDATEs de psql também ocorrem — só o banco vê
todos os caminhos de escrita.

Decisões do dono:
  - Gatilho: APENAS a transição para situacao = '15'.
  - Cancelados: uma vez em 15, fica contado pra sempre (ON CONFLICT DO NOTHING).
  - Rollout: em paralelo — a aba ainda usa em_andamento_data; o ledger entra
    numa coluna de comparação. A troca definitiva é follow-up.

`shipping_day` é DATE comum, computado no insert (trigger/backfill) — NÃO
GENERATED: `AT TIME ZONE` é STABLE, não IMMUTABLE, e quebraria a DDL.

Backfill inicial a partir do conjunto verde atual (proxy do histórico, já que
não temos o histórico de transições): `shipping_day` = `em_andamento_data`.
Downgrade dropa trigger + função + tabela (sem perda em bling_orders).
"""
# ruff: noqa: S608

from alembic import op

revision = "0156_bling_envio_evento"
down_revision = "0155_valuation_marketplace_saldo_diario"
branch_labels = None
depends_on = None

SCHEMA = "davinci"

# Espelha _SITUACAO_NAO_VERDE de app/routers/estoque.py: cancelamento/pré-envio
# + etiqueta 83965. O backfill usa o COMPLEMENTO disso (= o conjunto verde que a
# aba mostra hoje) como melhor proxy de "já enviou".
_SITUACAO_NAO_VERDE = (
    "6", "12", "21", "83955", "83962", "83966", "84686", "545901", "83965",
)


def upgrade() -> None:
    # 1) Tabela-ledger. PK (bling_id, item_index) = uma linha por item de pedido,
    #    igual ao grão de bling_orders (uniq 0111). Sem FK: imune ao DELETE do sync.
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.bling_envio_evento (
            bling_id     BIGINT  NOT NULL,
            item_index   INTEGER NOT NULL,
            item_codigo  TEXT,
            numero       TEXT,
            occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            shipping_day DATE NOT NULL,
            PRIMARY KEY (bling_id, item_index)
        )
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS ix_bling_envio_evento_shipping_day
            ON {SCHEMA}.bling_envio_evento (shipping_day)
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS ix_bling_envio_evento_item_codigo
            ON {SCHEMA}.bling_envio_evento (item_codigo)
        """
    )

    # 2) Backfill — conjunto verde atual como proxy do histórico. shipping_day =
    #    em_andamento_data; occurred_at = meio-dia BRT desse dia (>= 08:00, então
    #    não escorrega pro dia anterior).
    nao_verde = ", ".join(f"'{s}'" for s in _SITUACAO_NAO_VERDE)
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.bling_envio_evento
            (bling_id, item_index, item_codigo, numero, occurred_at, shipping_day)
        SELECT
            bling_id, item_index, item_codigo, numero,
            (em_andamento_data + time '12:00') AT TIME ZONE 'America/Sao_Paulo',
            em_andamento_data
        FROM {SCHEMA}.bling_orders
        WHERE bling_id IS NOT NULL
          AND item_index IS NOT NULL
          AND em_andamento_data IS NOT NULL
          AND situacao NOT IN ({nao_verde})
        ON CONFLICT (bling_id, item_index) DO NOTHING
        """
    )

    # 3) CAPTURE: dispara quando um pedido ENTRA na situação 15. Idempotente
    #    (ON CONFLICT DO NOTHING) — a 1ª entrada vence e nunca move, então o
    #    reinsert do sync (INSERT já em 15) e oscilações 15→15 não duplicam nem
    #    recarimbam. No UPDATE só dispara em <algo> → 15.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.bling_envio_evento_capture_fn()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.situacao = '15'
               AND NEW.bling_id IS NOT NULL
               AND NEW.item_index IS NOT NULL
               AND (TG_OP = 'INSERT' OR OLD.situacao IS DISTINCT FROM '15') THEN
                INSERT INTO {SCHEMA}.bling_envio_evento
                    (bling_id, item_index, item_codigo, numero,
                     occurred_at, shipping_day)
                VALUES (
                    NEW.bling_id, NEW.item_index, NEW.item_codigo, NEW.numero,
                    now(),
                    ((now() AT TIME ZONE 'America/Sao_Paulo')
                     - interval '8 hours')::date
                )
                ON CONFLICT (bling_id, item_index) DO NOTHING;
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        f"""
        DROP TRIGGER IF EXISTS bling_orders_envio_evento_capture
        ON {SCHEMA}.bling_orders
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER bling_orders_envio_evento_capture
        AFTER INSERT OR UPDATE OF situacao
        ON {SCHEMA}.bling_orders
        FOR EACH ROW
        EXECUTE FUNCTION {SCHEMA}.bling_envio_evento_capture_fn()
        """
    )


def downgrade() -> None:
    op.execute(
        f"DROP TRIGGER IF EXISTS bling_orders_envio_evento_capture "
        f"ON {SCHEMA}.bling_orders"
    )
    op.execute(
        f"DROP FUNCTION IF EXISTS {SCHEMA}.bling_envio_evento_capture_fn()"
    )
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.bling_envio_evento")
