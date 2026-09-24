"""redes_sociais.postagem_hora_inicio — a que horas o robô começa

Eduardo, 24/09/2026: "tem posts por dia e intervalo, mas onde boto que horas
ele começa esses posts?".

A pergunta expôs uma confusão que o nome da seção alimentava. "Publicação
automática" prometia um robô que publica sozinho, mas `postagem_max_dia` e
`postagem_intervalo_min` eram só GUARDA-CORPOS: recusavam agendamento apertado
demais, e nada mais. Quem escolhia o vídeo e a hora era sempre uma pessoa — as
14 postagens que existiam em produção tinham todas `origem = manual`.

Esta coluna é o que faltava pros três virarem um horário de trabalho: começa
às 18h, respeita o intervalo, para no teto do dia.

NULO = o robô NÃO publica sozinho nesta conta, mesmo com `postagem_auto`
ligado. É de propósito: ligar publicação autônoma tem que ser ato deliberado,
com hora escolhida. Se NULO valesse "começa à meia-noite", toda conta que já
tinha o interruptor ligado (por causa do agendamento manual, que é o que ele
significava até hoje) começaria a publicar sozinha no dia do deploy.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0316_hora_inicio_postagem"
down_revision: str | None = "0315_metricas_postagem"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "redes_sociais",
        sa.Column("postagem_hora_inicio", sa.SmallInteger(), nullable=True),
        schema=SCHEMA,
    )
    # 0-23, hora de Brasília. A trava fica no banco também, e não só no schema
    # do Pydantic: quem escreve direto no SQL (helper, correção à mão) erra do
    # mesmo jeito, e hora 25 viraria robô que nunca publica sem dizer por quê.
    op.create_check_constraint(
        "ck_redes_sociais_hora_inicio",
        "redes_sociais",
        "postagem_hora_inicio IS NULL OR (postagem_hora_inicio BETWEEN 0 AND 23)",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint("ck_redes_sociais_hora_inicio", "redes_sociais", schema=SCHEMA)
    op.drop_column("redes_sociais", "postagem_hora_inicio", schema=SCHEMA)
