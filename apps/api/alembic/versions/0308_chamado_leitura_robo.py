# ruff: noqa: S608
"""Leitura do caso na TELA pelo robô: quando ele leu, e quando a fila entregou

Vinicius, 22/09/2026 (chamado 441aa837, pedido 292592, Shopee Marquezini): a
abertura não tinha caminho pela API (`devolucao_sem_return`), o robô abriu o caso
no Portal de Atendimento ao Vendedor e devolveu o protocolo 2101308949814067207.
O Agente Shopee respondeu em 19/09 às 21:42 — e três dias depois o painel ainda
não sabia. Não era bug de leitura: NINGUÉM voltava naquela tela. O contrato do
robô só sabia ABRIR e RESPONDER; ler nunca existiu.

## Por que DUAS colunas e não uma "próxima leitura"

São duas perguntas diferentes e juntá-las apaga a mais importante:

- `leitura_robo_at` — a última leitura CONFIRMADA (o robô voltou e disse o que
  viu). É o que responde "esse caso está sendo acompanhado?" e é a âncora da
  cadência (3 h pra caso com fala recente, 24 h pra caso frio).
- `leitura_robo_claim_at` — a entrega em curso. Esconde o caso dos outros polls
  por 30 min e vence sozinha: robô que morre no meio não deixa tarefa presa, ao
  contrário do `enviando` das mensagens, que precisa do resgate do `_LEASE_STALE`.

Com um campo só, um robô que pede a fila e nunca volta ficaria indistinguível de
um robô que leu — que é exatamente o silêncio que criou este caso.

## Por que `chamado_de_tela` é uma COLUNA, e não um EXISTS sobre a abertura

A primeira versão deduzia "é caso de tela" por um EXISTS: existe abertura de canal
`robo` e status `enviada`? A revisão derrubou isso com um caso concreto. A varredura
`chamados_pendencias.varrer` REAPROVEITA a mensagem de abertura que já existia e só
troca o canal dela pra `robo` — sem tocar no número guardado em `chamados.chamado`.
Então um chamado que carrega um nº de API perfeitamente válido (claim do ML vindo da
Logística, `return_sn` que o `_disparar_shopee` gravou antes de desistir) passaria a
ser lido como "de tela" e sairia da varredura por API que FUNCIONAVA pra ele. Trocar
um acompanhamento que funciona por silêncio é pior que o bug original.

Quem sabe a verdade é quem ESCREVE o número: `/agent/resultado` e `/agent/registrar`,
os dois pontos em que o robô devolve um protocolo capturado na tela. Eles passam a
marcar a coluna. O backfill abaixo reconstrói o passado com o mesmo critério exato, e
não por semelhança.

## Por que NÃO há backfill de `chamados.canal`

A tentação era virar `canal='api'` → `'robo'` nos casos abertos na tela, já que
`chamados_pendencias.varrer` marca só o canal da MENSAGEM enquanto
`chamados_devolucao._encaminhar_robo` marca também o do CHAMADO. Seria errado:
com `canal='robo'`, `chamados.status_e_motivo_da_aba` manda a linha pra "Análise
Robô" quando a plataforma responde — e não existe cérebro de Shopee (o
`/agent/analisar` tem `plataforma="ml"` por padrão). A resposta que acabamos de
resgatar cairia num estado que ninguém tria, parecendo atendida. Pior que hoje.

Quem separa os dois mundos passa a ser `chamados.chamado_de_tela`. Ele tira esses
casos da varredura por API (que falhava de hora em hora contra a Shopee) e os entrega
à fila de leitura — sem mexer no que a equipe vê.

O índice é parcial e seu predicado é ESCRITO IGUAL ao da consulta da fila: o Postgres
não prova implicação entre `resolvido IS false` e `resolvido = false`, nem entre
`coalesce(trim(x),'') <> ''` e `x IS NOT NULL`, então um índice "equivalente" mas
escrito diferente vira peso morto.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0308_chamado_leitura_robo"
down_revision: str | None = "0307_ouvidoria_robo_arquivado"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "chamados",
        sa.Column("leitura_robo_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "chamados",
        sa.Column("leitura_robo_claim_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "chamados",
        sa.Column(
            "chamado_de_tela",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema=SCHEMA,
    )

    # BACKFILL — exato, não por semelhança. Quando o `/agent/resultado` grava o
    # protocolo que o robô capturou na tela, ele deixa no histórico a frase
    # "Protocolo <nº> capturado pelo robô" (routers/chamados.py). Essa frase é a
    # assinatura de que o número em `chamados.chamado` veio da TELA: ela só é
    # escrita no mesmo `if` que grava o número. Casar a frase COM o número atual
    # ainda protege do caso em que alguém trocou o protocolo depois.
    #
    # O caso que originou tudo (chamado 441aa837, pedido 292592, protocolo
    # 2101308949814067207) tem exatamente essa linha no histórico.
    res = op.get_bind().execute(
        sa.text(
            f"""
            UPDATE {SCHEMA}.chamados c
               SET chamado_de_tela = true
             WHERE coalesce(trim(c.chamado), '') <> ''
               AND EXISTS (
                   SELECT 1 FROM {SCHEMA}.chamado_mensagem m
                    WHERE m.chamado_id = c.id
                      AND m.tipo = 'sistema'
                      AND m.texto = 'Protocolo ' || c.chamado || ' capturado pelo robô'
               )
            """
        )
    )
    print(f"[0308] chamados marcados como abertos na tela: {res.rowcount}")

    # Parcial, com o predicado escrito EXATAMENTE como a consulta da fila o
    # escreve (services/chamados_leitura.fila) — senão o planejador não usa.
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS ix_chamados_leitura_robo
            ON {SCHEMA}.chamados (leitura_robo_at NULLS FIRST, created_at)
         WHERE chamado_de_tela IS true AND resolvido IS false
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_chamados_leitura_robo")
    op.drop_column("chamados", "chamado_de_tela", schema=SCHEMA)
    op.drop_column("chamados", "leitura_robo_claim_at", schema=SCHEMA)
    op.drop_column("chamados", "leitura_robo_at", schema=SCHEMA)
