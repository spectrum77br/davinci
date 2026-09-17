"""dm_conversas + dm_mensagens — robô de resposta das DMs do Instagram

Eduardo, 16/09/2026: "queria adicionar a ideia de responder automaticamente
também mensagens enviadas nos nossos 4 que temos rede social".

O espelho invertido do robô de postagem (0281): lá NÓS chamamos a Meta; aqui
a META nos chama por webhook, e do outro lado da conversa tem um terceiro.

  • `dm_conversas`: uma por (conta da marca, IGSID do cliente). O IGSID é o
    id da pessoa NO ESCOPO DAQUELA CONTA — não é o @ e não aponta pra
    pedido nenhum. `ultima_recebida_em` é o relógio da janela de 24h da
    Meta, que é a restrição real (não o rate limit).
  • `dm_mensagens`: entrada imutável E fila de saída na mesma linha, no
    vocabulário de `chamado_mensagem`. `mid` UNIQUE é a idempotência do
    webhook NO BANCO: a Meta reentrega por horas enquanto não vir 200, e um
    deploy reiniciando a api já devolve não-200 — reentrega é rotina, não
    exceção.
  • Índice único PARCIAL `uq_dm_resposta_em_voo`: uma resposta em voo por
    conversa. É ele, sozinho, que impede o robô de responder duas vezes à
    mesma pessoa — dois workers podem tentar, só um grava. Declarado aqui E
    no model, como manda o precedente da 0281.

Esta migration só cria as tabelas. Nada envia mensagem: a rota do webhook
que vem junto apenas valida assinatura e grava.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0288_dm_instagram"
down_revision: str | None = "0287_chamado_status_plataforma"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    # As FKs desta migration pegam ShareRowExclusiveLock em `redes_sociais` e
    # `users`, com a api rodando. Sem timeout, uma transação longa faria a
    # migration esperar de graça e enfileirar todo mundo atrás dela.
    op.execute("SET lock_timeout = '3s'")
    op.create_table(
        "dm_conversas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # SET NULL, não CASCADE: apagar a conta não pode apagar o histórico da
        # conversa — por isso plataforma/conta ficam em snapshot abaixo.
        sa.Column(
            "rede_social_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.redes_sociais.id", ondelete="SET NULL",
                name="fk_dm_conversas_rede_social_id_redes_sociais",
            ),
            nullable=True,
        ),
        sa.Column(
            "plataforma", sa.String(32), nullable=False,
            server_default=sa.text("'instagram'"),
        ),
        sa.Column("conta", sa.Text(), nullable=True),
        sa.Column("participante_id", sa.String(128), nullable=False),
        sa.Column("participante_nome", sa.Text(), nullable=True),
        sa.Column("ultima_recebida_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultima_enviada_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default=sa.text("'aberta'"),
        ),
        # Desliga o robô NESTA conversa sem silenciar a pessoa. É o que a
        # palavra de escape (ATENDENTE) vira — e é exigência de política da
        # Meta: experiência automatizada precisa de caminho para humano.
        sa.Column("auto", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "avisada_automacao", sa.Boolean(), nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "assumido_por", postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.users.id", ondelete="SET NULL",
                name="fk_dm_conversas_assumido_por_users",
            ),
            nullable=True,
        ),
        sa.Column("assumido_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.UniqueConstraint(
            "rede_social_id", "participante_id",
            name="uq_dm_conversas_conta_participante",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_dm_conversas_rede_social_id", "dm_conversas", ["rede_social_id"], schema=SCHEMA,
    )
    op.create_index(
        "ix_dm_conversas_participante_id", "dm_conversas", ["participante_id"], schema=SCHEMA,
    )
    # O cron de resposta varre por aqui: conversa com mensagem nova dentro da
    # janela de 24h.
    op.create_index(
        "ix_dm_conversas_ultima_recebida_em", "dm_conversas",
        ["ultima_recebida_em"], schema=SCHEMA,
    )

    op.create_table(
        "dm_mensagens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "conversa_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.dm_conversas.id", ondelete="CASCADE",
                name="fk_dm_mensagens_conversa_id_dm_conversas",
            ),
            nullable=False,
        ),
        # UNIQUE e nullable: entrada sempre tem `mid`; saída só ganha depois
        # de enviada, e o Postgres deixa vários NULL no índice único.
        sa.Column("mid", sa.String(191), nullable=True),
        sa.Column("direcao", sa.String(16), nullable=False),
        sa.Column(
            "tipo", sa.String(16), nullable=False, server_default=sa.text("'texto'"),
        ),
        sa.Column("texto", sa.Text(), nullable=True),
        sa.Column("anexo_tipo", sa.String(32), nullable=True),
        sa.Column("anexo_url", sa.Text(), nullable=True),
        sa.Column(
            "payload", postgresql.JSONB(), nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("ocorrido_em", sa.DateTime(timezone=True), nullable=True),
        # O cliente apagou. A Meta exige que a gravação do App Review mostre
        # tratamento de "unsent" — e o histórico tem que refletir, não fingir
        # que a mensagem continua lá.
        sa.Column("apagada_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default=sa.text("'recebida'"),
        ),
        sa.Column("resposta_modelo_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "tentativas_max", sa.Integer(), nullable=False, server_default=sa.text("2"),
        ),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enviada_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.UniqueConstraint("mid", name="uq_dm_mensagens_mid"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_dm_mensagens_conversa_id", "dm_mensagens", ["conversa_id"], schema=SCHEMA,
    )
    op.create_index(
        "ix_dm_mensagens_ocorrido_em", "dm_mensagens", ["ocorrido_em"], schema=SCHEMA,
    )
    # UMA resposta em voo por conversa. Mensagem enviada não se desfaz, e aqui
    # não existe `container_id` pra perguntar "será que saiu?" como na
    # postagem — então a trava é este índice, no banco, não na aplicação.
    op.create_index(
        "uq_dm_resposta_em_voo",
        "dm_mensagens",
        ["conversa_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pendente', 'enviando')"),
        schema=SCHEMA,
    )


    # Credencial de MENSAGEM, separada de `redes_sociais_tokens`: aquela é
    # lida por seis lugares que publicam Reels em produção, todos esperando
    # UMA linha por conta. Token de publicação (Página, não expira) e token de
    # mensagem (usuário do Instagram, ~60 dias) são objetos diferentes.
    op.create_table(
        "dm_contas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "rede_social_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.redes_sociais.id", ondelete="CASCADE",
                name="fk_dm_contas_rede_social_id_redes_sociais",
            ),
            nullable=False,
        ),
        sa.Column("ig_user_id", sa.String(128), nullable=False),
        sa.Column("token_enc", sa.LargeBinary(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'ok'")),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_ok_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.UniqueConstraint("rede_social_id", name="uq_dm_contas_rede_social_id"),
        schema=SCHEMA,
    )
    # UNIQUE, não só índice: é a chave pela qual o webhook resolve a marca.
    op.create_index(
        "ix_dm_contas_ig_user_id", "dm_contas", ["ig_user_id"], unique=True, schema=SCHEMA,
    )
    # Em claro pro cron achar o que vence sem decifrar: 60 dias passam rápido
    # e token vencido emudece o robô em silêncio.
    op.create_index(
        "ix_dm_contas_token_expires_at", "dm_contas", ["token_expires_at"], schema=SCHEMA,
    )

    # Interruptor por conta, espelhando `postagem_auto`: ligar uma marca de
    # cada vez sem deploy. Nasce desligado.
    op.add_column(
        "redes_sociais",
        sa.Column("dm_auto", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("redes_sociais", "dm_auto", schema=SCHEMA)
    op.drop_index("ix_dm_contas_token_expires_at", table_name="dm_contas", schema=SCHEMA)
    op.drop_index("ix_dm_contas_ig_user_id", table_name="dm_contas", schema=SCHEMA)
    op.drop_table("dm_contas", schema=SCHEMA)
    op.drop_index("uq_dm_resposta_em_voo", table_name="dm_mensagens", schema=SCHEMA)
    op.drop_index("ix_dm_mensagens_ocorrido_em", table_name="dm_mensagens", schema=SCHEMA)
    op.drop_index("ix_dm_mensagens_conversa_id", table_name="dm_mensagens", schema=SCHEMA)
    op.drop_table("dm_mensagens", schema=SCHEMA)
    op.drop_index(
        "ix_dm_conversas_ultima_recebida_em", table_name="dm_conversas", schema=SCHEMA,
    )
    op.drop_index(
        "ix_dm_conversas_participante_id", table_name="dm_conversas", schema=SCHEMA,
    )
    op.drop_index("ix_dm_conversas_rede_social_id", table_name="dm_conversas", schema=SCHEMA)
    op.drop_table("dm_conversas", schema=SCHEMA)
