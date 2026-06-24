"""bling_envio_correcao — re-carimba o dia na correção de erro + fila p/ aviso

Cenário do dono: às vezes o operador erra no Bling e põe um pedido em "em
andamento" (situação 15) no dia errado; depois volta pra "em aberto" e, noutro
dia, põe em 15 de novo (o certo). A regra do 0156 ("primeira entrada vence")
NÃO duplicava (PK bling_id+item_index + ON CONFLICT DO NOTHING), mas deixava o
envio grudado no dia ERRADO — a correção do dia seguinte não movia.

Esta migration troca a função do trigger para:
  - 1ª aparição em 15 → cria a linha (igual antes; reinsert do sync e
    primeira transição). Imune ao sync porque o reinsert é INSERT e a linha do
    ledger sobrevive ao DELETE (achada via SELECT).
  - RE-entrada GENUÍNA em 15 (UPDATE, OLD != 15) vinda de estado NÃO-enviado
    (pré-envio/cancel) e com dia diferente → RE-CARIMBA o shipping_day pro dia
    da correção. Continua UMA linha só (sem duplicar).
  - Oscilação vinda de estado JÁ-enviado (Entregue 83953 → 15 etc.) e reinsert
    do sync (TG_OP=INSERT) → NÃO mexem (preserva o dia real).

E grava cada correção numa fila `bling_envio_correcao` (PK bling_id +
dia_anterior + dia_novo → dedup: pedido multi-item = 1 aviso). Uma rotina
externa drena `threema_sent_at IS NULL` e avisa via Threema.

Não mexe na tabela/trigger do 0156 além de trocar o corpo da função (mesmo
nome, mesmo trigger). downgrade restaura a função do 0156 e dropa a fila.
"""
# ruff: noqa: S608

from alembic import op

revision = "0157_bling_envio_correcao"
down_revision = "0156_bling_envio_evento"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    # 1) Fila (outbox) das correções de dia. Sem FK (igual ledger). PK natural
    #    (bling_id, dia_anterior, dia_novo) dedup multi-item num aviso só.
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.bling_envio_correcao (
            bling_id        BIGINT NOT NULL,
            dia_anterior    DATE   NOT NULL,
            dia_novo        DATE   NOT NULL,
            numero          TEXT,
            item_codigo     TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            threema_sent_at TIMESTAMPTZ,
            PRIMARY KEY (bling_id, dia_anterior, dia_novo)
        )
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS ix_bling_envio_correcao_pendente
            ON {SCHEMA}.bling_envio_correcao (created_at)
            WHERE threema_sent_at IS NULL
        """
    )

    # 2) Nova lógica do trigger (mesmo nome de função/trigger do 0156).
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.bling_envio_evento_capture_fn()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            v_today date := ((now() AT TIME ZONE 'America/Sao_Paulo')
                             - interval '8 hours')::date;
            v_old_day date;
            v_found boolean;
        BEGIN
            IF NEW.situacao IS DISTINCT FROM '15'
               OR NEW.bling_id IS NULL OR NEW.item_index IS NULL THEN
                RETURN NULL;
            END IF;

            SELECT shipping_day INTO v_old_day
            FROM {SCHEMA}.bling_envio_evento
            WHERE bling_id = NEW.bling_id AND item_index = NEW.item_index;
            v_found := FOUND;

            IF NOT v_found THEN
                -- 1ª aparição em 15 (INSERT inicial ou 1ª transição). O reinsert
                -- do sync acha v_found=true (ledger sobrevive ao DELETE) e cai fora.
                INSERT INTO {SCHEMA}.bling_envio_evento
                    (bling_id, item_index, item_codigo, numero,
                     occurred_at, shipping_day)
                VALUES (NEW.bling_id, NEW.item_index, NEW.item_codigo, NEW.numero,
                        now(), v_today)
                ON CONFLICT (bling_id, item_index) DO NOTHING;
                RETURN NULL;
            END IF;

            -- Já contado. Só re-carimba o dia numa transição GENUÍNA pra 15
            -- (UPDATE, OLD != 15) vinda de estado NÃO-enviado (pré-envio/cancel) —
            -- o operador errou, voltou pra "em aberto" e relançou noutro dia.
            -- Reinsert do sync (TG_OP=INSERT) e oscilação vinda de estado
            -- já-enviado (Entregue->15) NÃO mexem.
            IF TG_OP = 'UPDATE'
               AND OLD.situacao IS DISTINCT FROM '15'
               AND (OLD.situacao IS NULL OR OLD.situacao IN
                    ('6','12','21','83955','83962','83966','84686','545901','83965'))
               AND v_old_day IS DISTINCT FROM v_today THEN
                UPDATE {SCHEMA}.bling_envio_evento
                SET shipping_day = v_today, occurred_at = now(),
                    item_codigo = NEW.item_codigo, numero = NEW.numero
                WHERE bling_id = NEW.bling_id AND item_index = NEW.item_index;

                INSERT INTO {SCHEMA}.bling_envio_correcao
                    (bling_id, numero, item_codigo, dia_anterior, dia_novo)
                VALUES (NEW.bling_id, NEW.numero, NEW.item_codigo, v_old_day, v_today)
                ON CONFLICT (bling_id, dia_anterior, dia_novo) DO NOTHING;
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )


def downgrade() -> None:
    # Restaura a função do 0156 (1ª entrada vence; sem re-stamp/fila).
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
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.bling_envio_correcao")
