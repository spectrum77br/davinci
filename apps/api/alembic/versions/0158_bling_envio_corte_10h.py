"""corte do dia-envio 08:00 → 10:00 (absorve o lag de processamento)

Validação contra o relatório do Bling (24/06): dos 12 pedidos que não caíam no
bucket 24/06, 10 eram do lote da manhã que o Bling carimbou ~07:27 mas o DaVinci
só CAPTUROU ~08:21 (lag ~54min). Como `occurred_at`=08:21 passou das 08:00, o
`−8h` jogava pro dia seguinte. O DaVinci só tem a hora em que ELE processou, não
a hora do carimbo no Bling — então o corte precisa ficar DEPOIS da janela de
processamento da manhã (vista até ~08:21). Com corte às 10:00 os 10 voltam pro
dia certo: 197 + 10 = 207 = relatório do Bling. (Os outros 2 do gap eram ruído
"Revisar" da própria planilha — pedidos velhos em 83957/83960, não-envios.)

Troca `interval '8 hours'` por `'10 hours'` na função do trigger e RE-DERIVA o
`shipping_day` de todas as linhas existentes a partir do `occurred_at`. O
backfill NÃO é afetado: occurred_at=meio-dia BRT, e meio-dia − 10h = 02:00 do
mesmo dia → ::date inalterado (= em_andamento_data). Só as capturas AO VIVO
(occurred_at real) são re-bucketizadas.
"""
# ruff: noqa: S608

from alembic import op

revision = "0158_bling_envio_corte_10h"
down_revision = "0157_bling_envio_correcao"
branch_labels = None
depends_on = None

SCHEMA = "davinci"

_CUTOFF_FN = """
CREATE OR REPLACE FUNCTION {schema}.bling_envio_evento_capture_fn()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_today date := ((now() AT TIME ZONE 'America/Sao_Paulo')
                     - interval '{h} hours')::date;
    v_old_day date;
    v_found boolean;
BEGIN
    IF NEW.situacao IS DISTINCT FROM '15'
       OR NEW.bling_id IS NULL OR NEW.item_index IS NULL THEN
        RETURN NULL;
    END IF;

    SELECT shipping_day INTO v_old_day
    FROM {schema}.bling_envio_evento
    WHERE bling_id = NEW.bling_id AND item_index = NEW.item_index;
    v_found := FOUND;

    IF NOT v_found THEN
        INSERT INTO {schema}.bling_envio_evento
            (bling_id, item_index, item_codigo, numero, occurred_at, shipping_day)
        VALUES (NEW.bling_id, NEW.item_index, NEW.item_codigo, NEW.numero,
                now(), v_today)
        ON CONFLICT (bling_id, item_index) DO NOTHING;
        RETURN NULL;
    END IF;

    IF TG_OP = 'UPDATE'
       AND OLD.situacao IS DISTINCT FROM '15'
       AND (OLD.situacao IS NULL OR OLD.situacao IN
            ('6','12','21','83955','83962','83966','84686','545901','83965'))
       AND v_old_day IS DISTINCT FROM v_today THEN
        UPDATE {schema}.bling_envio_evento
        SET shipping_day = v_today, occurred_at = now(),
            item_codigo = NEW.item_codigo, numero = NEW.numero
        WHERE bling_id = NEW.bling_id AND item_index = NEW.item_index;

        INSERT INTO {schema}.bling_envio_correcao
            (bling_id, numero, item_codigo, dia_anterior, dia_novo)
        VALUES (NEW.bling_id, NEW.numero, NEW.item_codigo, v_old_day, v_today)
        ON CONFLICT (bling_id, dia_anterior, dia_novo) DO NOTHING;
    END IF;
    RETURN NULL;
END;
$$
"""

_REDERIVE = """
UPDATE {schema}.bling_envio_evento
SET shipping_day = ((occurred_at AT TIME ZONE 'America/Sao_Paulo')
                    - interval '{h} hours')::date
WHERE shipping_day IS DISTINCT FROM
      ((occurred_at AT TIME ZONE 'America/Sao_Paulo') - interval '{h} hours')::date
"""


def upgrade() -> None:
    op.execute(_CUTOFF_FN.format(schema=SCHEMA, h=10))
    op.execute(_REDERIVE.format(schema=SCHEMA, h=10))


def downgrade() -> None:
    op.execute(_CUTOFF_FN.format(schema=SCHEMA, h=8))
    op.execute(_REDERIVE.format(schema=SCHEMA, h=8))
