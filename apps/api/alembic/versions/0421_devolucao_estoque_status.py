"""Devoluções: a linha guarda se a entrada no estoque do Bling foi CONFIRMADA

Caso 300588 (15/10/2026): a mala voltou como Novo, o Bling recusou a entrada
(limite de chamadas) e a tela mostrou a "Data devolvido estoque" como se
tivesse entrado — a data era carimbada ANTES de chamar o Bling e o erro sumia
num aviso de 6 s. No mesmo dia, 300334 e 298826: o Bling GRAVOU a entrada mas
respondeu 504, e o DaVinci tratou como falha (sem registro, sem estorno).
OK do Eduardo em 15/10 ("faça o conserto no localhost e me manda os prints").

- estoque_status: ok | falhou | conferindo | sem_confirmacao (NULL = não se
  aplica, ou linha de antes desta migration — com estoque_mov_* preenchido ela
  conta como ok).
- estoque_erro: o motivo, em português, mostrado na linha.
- estoque_tentativa_at: quando o DaVinci mandou a entrada (a data de
  devolvido continua sendo o pedido).
- estoque_conferir_*: o produto/ação da entrada que o Bling respondeu com erro
  DEPOIS de receber (5xx/timeout) — o worker confere no espelho de movimentos.
- estoque_auto_tentativas: quantas vezes o worker tentou de novo sozinho a
  entrada que o Bling recusou por excesso de chamadas (até 5; 21/10).
- estoque_mov_ref: o movimento (stock_movements.id) que a conferência usou,
  para o mesmo movimento não confirmar duas linhas.

Linhas de 02/06 em diante (quando o registro do movimento começou) com
"devolver estoque" ligado, data e SEM movimento registrado (18 em produção em
15/10):
- as que têm UMA entrada do mesmo produto e quantidade no espelho
  (stock_movements) até 2 min depois do pedido, que nenhuma outra devolução
  nem o robô de prioridade explica, ENTRARAM — viram ok com o movimento
  registrado (4 em 15/10: 288837 a003.pi/a004.pi, 290151 b069.14/b069.24);
- o resto vira `sem_confirmacao`: a tela avisa "confira no Bling" em vez de
  mostrar a data como se tivesse entrado.
Antes de 02/06 nenhuma linha tinha registro — ficam como estão. Só ADD COLUMN
nulo + UPDATEs de poucas linhas; lock_timeout 3 s (rodar fora de :20/:50, o
devolucao_prazo_sync segura a tabela por ~1 min).

Revision ID: 0421_devolucao_estoque_status
Revises: 0420_atendimento_agente
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0421_devolucao_estoque_status"
down_revision: str | None = "0420_atendimento_agente"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
STATUS = ("ok", "falhou", "conferindo", "sem_confirmacao")
# Primeiro movimento registrado em produção (estoque_mov_*, alembic 0115).
INICIO_REGISTRO = "2026-06-02 11:09:27+00"
ERRO_LEGADO = (
    "Lançamento sem confirmação do Bling — confira o extrato do SKU antes de tentar de novo."
)


# Entrou de fato: uma única entrada E do produto de destino, mesma quantidade,
# até 2 min depois do pedido, sem outra devolução, outra linha deste backfill
# ou o robô de prioridade na janela (na dúvida, fica sem_confirmacao).
_SQL_ENTRARAM = f"""
WITH alvo AS (
    SELECT d.id, d.data_devolvido_estoque AS t0,
           GREATEST(1, COALESCE(d.quantidade, 1)) AS qty,
           btrim(COALESCE(NULLIF(btrim(d.estoque_destino_sku), ''), d.sku)) AS sku_dest
      FROM {SCHEMA}.devolutions d
     WHERE d.estoque_mov_bling_id IS NULL
       AND d.devolver_estoque
       AND d.data_devolvido_estoque >= CAST(:inicio AS timestamptz)
       AND (d.condicao_produto IN ('Novo', 'Usado', 'Trocado')
            OR (d.condicao_produto = 'Manutenção' AND d.manutencao_destino IN ('Novo', 'Usado')))
), prod AS (
    SELECT a.*,
           (SELECT p.bling_product_id FROM {SCHEMA}.products p
             WHERE lower(btrim(p.sku)) = lower(a.sku_dest) AND p.bling_product_id IS NOT NULL
             ORDER BY (p.situacao = 'A') DESC NULLS LAST LIMIT 1) AS pid
      FROM alvo a
     WHERE COALESCE(a.sku_dest, '') <> ''
), cand AS (
    SELECT pr.id, pr.pid, pr.sku_dest, pr.qty, min(sm.id::text)::uuid AS mov
      FROM prod pr
      JOIN {SCHEMA}.stock_movements sm
        ON sm.bling_product_id = pr.pid AND sm.tipo = 'E' AND sm.quantidade = pr.qty
       AND sm.date >= pr.t0 AND sm.date <= pr.t0 + interval '2 minutes'
     WHERE pr.pid IS NOT NULL
       AND NOT EXISTS (
            SELECT 1 FROM {SCHEMA}.devolutions o
             WHERE o.id <> pr.id AND o.estoque_mov_bling_id = pr.pid
               AND o.data_devolvido_estoque
                   BETWEEN pr.t0 - interval '2 minutes' AND pr.t0 + interval '2 minutes')
       AND NOT EXISTS (
            SELECT 1 FROM prod o
             WHERE o.id <> pr.id AND o.pid = pr.pid
               AND o.t0 BETWEEN pr.t0 - interval '2 minutes' AND pr.t0 + interval '2 minutes')
       AND NOT EXISTS (
            SELECT 1 FROM {SCHEMA}.prioridade_estoque_movimentos m
             WHERE m.bling_product_id = pr.pid AND m.operacao = 'E'
               AND COALESCE(m.lancado_at, m.updated_at)
                   BETWEEN pr.t0 - interval '2 minutes' AND pr.t0 + interval '2 minutes')
     GROUP BY 1, 2, 3, 4
    HAVING count(*) = 1
)
UPDATE {SCHEMA}.devolutions d
   SET estoque_status = 'ok',
       estoque_mov_sku = c.sku_dest,
       estoque_mov_bling_id = c.pid,
       estoque_mov_action = 'entry_conferida_backfill',
       estoque_mov_qty = c.qty,
       estoque_mov_ref = c.mov
  FROM cand c
 WHERE d.id = c.id
"""  # noqa: S608


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.add_column(
        "devolutions", sa.Column("estoque_status", sa.Text(), nullable=True), schema=SCHEMA
    )
    op.add_column("devolutions", sa.Column("estoque_erro", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column(
        "devolutions",
        sa.Column("estoque_tentativa_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "devolutions",
        sa.Column("estoque_conferir_bling_id", sa.BigInteger(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "devolutions", sa.Column("estoque_conferir_sku", sa.Text(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "devolutions", sa.Column("estoque_conferir_action", sa.Text(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "devolutions",
        sa.Column("estoque_mov_ref", pg.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    # Quantas vezes o worker já tentou de novo sozinho a entrada que o Bling
    # recusou por excesso de chamadas (21/10, caso 297390). Default constante:
    # não reescreve a tabela.
    op.add_column(
        "devolutions",
        sa.Column(
            "estoque_auto_tentativas",
            sa.SmallInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_devolutions_estoque_status"),
        "devolutions",
        "estoque_status IS NULL OR estoque_status IN (" + ", ".join(f"'{v}'" for v in STATUS) + ")",
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_devolutions_estoque_conferindo"),
        "devolutions",
        ["estoque_tentativa_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("estoque_status = 'conferindo'"),
    )
    op.execute(sa.text(_SQL_ENTRARAM).bindparams(inicio=INICIO_REGISTRO))
    op.execute(
        sa.text(
            f"""
            UPDATE {SCHEMA}.devolutions
               SET estoque_status = 'sem_confirmacao',
                   estoque_erro = :erro
             WHERE estoque_mov_bling_id IS NULL
               AND estoque_status IS NULL
               AND devolver_estoque
               AND data_devolvido_estoque >= CAST(:inicio AS timestamptz)
               AND (
                    condicao_produto IN ('Novo', 'Usado', 'Trocado')
                    OR (condicao_produto = 'Manutenção'
                        AND manutencao_destino IN ('Novo', 'Usado'))
               )
            """  # noqa: S608
        ).bindparams(erro=ERRO_LEGADO, inicio=INICIO_REGISTRO)
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    # Desfaz o registro que o backfill deu às linhas que entraram (o resto do
    # estoque_mov_* é de antes desta migration e fica).
    op.execute(
        sa.text(
            f"""
            UPDATE {SCHEMA}.devolutions
               SET estoque_mov_sku = NULL, estoque_mov_bling_id = NULL,
                   estoque_mov_action = NULL, estoque_mov_qty = NULL
             WHERE estoque_mov_action = 'entry_conferida_backfill'
            """  # noqa: S608
        )
    )
    op.drop_index(
        op.f("ix_devolutions_estoque_conferindo"), table_name="devolutions", schema=SCHEMA
    )
    op.drop_constraint(
        op.f("ck_devolutions_estoque_status"), "devolutions", type_="check", schema=SCHEMA
    )
    for col in (
        "estoque_auto_tentativas",
        "estoque_mov_ref",
        "estoque_conferir_action",
        "estoque_conferir_sku",
        "estoque_conferir_bling_id",
        "estoque_tentativa_at",
        "estoque_erro",
        "estoque_status",
    ):
        op.drop_column("devolutions", col, schema=SCHEMA)
