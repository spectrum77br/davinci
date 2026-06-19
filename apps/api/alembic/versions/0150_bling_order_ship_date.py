"""bling_order_ship_date — trava definitiva contra re-carimbo de em_andamento_data

A câmera 0135 identificou o culpado: o sync diário `bling_daily_sync.py`
(scheduled-task faturamento-3meses-pdf, ~05h BRT) recria cada pedido alterado
nas últimas 48h via DELETE+INSERT. O INSERT carimbava `em_andamento_data` com
HOJE (situação 15) ou NULL, descartando a data real — daí pedidos "pulando
pra hoje" toda manhã. Como é INSERT (não UPDATE), o trigger
`protect_em_andamento_data` (BEFORE UPDATE) não pegava.

O fix de fonte (preservar a data no próprio sync) resolve o caminho atual, mas
o ship-date não pode depender de uma linha que QUALQUER sync pode apagar. Esta
migration cria a defesa de banco que sobrevive a qualquer DELETE+reinsert:

- `davinci.bling_order_ship_date`: tabela persistente (numero PK, SEM FK pra
  bling_orders → não some no DELETE) que vira a fonte de verdade do ship-date.
- Trigger CAPTURE (AFTER INSERT/UPDATE OF em_andamento_data): toda data real
  gravada (transição → 15, correção manual, confirmação) é salva aqui.
  Mudanças intencionais sempre vencem (sobrescrevem o valor salvo).
- Trigger INHERIT (BEFORE INSERT): se o pedido chega num reinsert anônimo —
  sem data, ou com uma data >= a salva (i.e. "pulou pra hoje/frente") — e já
  existe ship-date salvo, herda o valor salvo. Brand-new (sem nada salvo) e
  backfills com data mais antiga passam intactos.

Não toca o caminho de UPDATE: confirmação 83965→15 e "mova os X do dia Y pro Z"
continuam movendo a data de propósito (e a CAPTURE registra o novo valor).

Backfill inicial a partir das datas atuais (curadas manualmente pelo operador).
Downgrade dropa triggers + funções + tabela (sem perda em bling_orders).
"""
# ruff: noqa: S608

from alembic import op

revision = "0150_bling_order_ship_date"
down_revision = "0149_segments_dimensions"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    # 1) Tabela persistente — fonte de verdade do ship-date.
    #    Propositalmente SEM FK pra bling_orders: o DELETE+reinsert do sync não
    #    pode levar essa linha junto.
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.bling_order_ship_date (
            numero TEXT PRIMARY KEY,
            bling_id BIGINT,
            em_andamento_data DATE NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # 2) Backfill — datas atuais (já corrigidas pelo operador). Pega a MAIS
    #    ANTIGA não-nula por numero, imune a qualquer linha mis-carimbada hoje.
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.bling_order_ship_date
            (numero, bling_id, em_andamento_data, updated_at)
        SELECT DISTINCT ON (numero)
            numero, bling_id, em_andamento_data, now()
        FROM {SCHEMA}.bling_orders
        WHERE numero IS NOT NULL AND em_andamento_data IS NOT NULL
        ORDER BY numero, em_andamento_data ASC
        ON CONFLICT (numero) DO NOTHING
        """
    )

    # 3) CAPTURE: toda data real gravada em bling_orders vira a verdade salva.
    #    Intencional vence (sempre sobrescreve); só bumpa updated_at se mudou.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.bling_order_ship_date_capture_fn()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.numero IS NOT NULL AND NEW.em_andamento_data IS NOT NULL THEN
                INSERT INTO {SCHEMA}.bling_order_ship_date
                    (numero, bling_id, em_andamento_data, updated_at)
                VALUES
                    (NEW.numero, NEW.bling_id, NEW.em_andamento_data, now())
                ON CONFLICT (numero) DO UPDATE
                    SET em_andamento_data = EXCLUDED.em_andamento_data,
                        bling_id = EXCLUDED.bling_id,
                        updated_at = now()
                    WHERE {SCHEMA}.bling_order_ship_date.em_andamento_data
                          IS DISTINCT FROM EXCLUDED.em_andamento_data;
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        f"""
        DROP TRIGGER IF EXISTS bling_orders_ship_date_capture
        ON {SCHEMA}.bling_orders
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER bling_orders_ship_date_capture
        AFTER INSERT OR UPDATE OF em_andamento_data
        ON {SCHEMA}.bling_orders
        FOR EACH ROW
        EXECUTE FUNCTION {SCHEMA}.bling_order_ship_date_capture_fn()
        """
    )

    # 4) INHERIT: reinsert anônimo herda a data salva. Roda BEFORE INSERT, então
    #    a CAPTURE (AFTER) e a câmera 0135 já enxergam a data curada.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.bling_order_ship_date_inherit_fn()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            v_saved DATE;
        BEGIN
            IF NEW.numero IS NULL THEN
                RETURN NEW;
            END IF;
            SELECT em_andamento_data INTO v_saved
            FROM {SCHEMA}.bling_order_ship_date
            WHERE numero = NEW.numero;
            -- Só herda no caso anônimo: sem data, ou data que "pulou pra
            -- frente" (>= salva, ex.: hoje). Brand-new (v_saved NULL) e
            -- backfill com data mais antiga passam intactos.
            IF v_saved IS NOT NULL
               AND (NEW.em_andamento_data IS NULL
                    OR NEW.em_andamento_data >= v_saved) THEN
                NEW.em_andamento_data := v_saved;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        f"""
        DROP TRIGGER IF EXISTS bling_orders_ship_date_inherit
        ON {SCHEMA}.bling_orders
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER bling_orders_ship_date_inherit
        BEFORE INSERT
        ON {SCHEMA}.bling_orders
        FOR EACH ROW
        EXECUTE FUNCTION {SCHEMA}.bling_order_ship_date_inherit_fn()
        """
    )


def downgrade() -> None:
    op.execute(
        f"DROP TRIGGER IF EXISTS bling_orders_ship_date_inherit "
        f"ON {SCHEMA}.bling_orders"
    )
    op.execute(
        f"DROP TRIGGER IF EXISTS bling_orders_ship_date_capture "
        f"ON {SCHEMA}.bling_orders"
    )
    op.execute(
        f"DROP FUNCTION IF EXISTS {SCHEMA}.bling_order_ship_date_inherit_fn()"
    )
    op.execute(
        f"DROP FUNCTION IF EXISTS {SCHEMA}.bling_order_ship_date_capture_fn()"
    )
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.bling_order_ship_date")
