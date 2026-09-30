"""atendimento_*: a caixa de atendimento unificado (Shopee, ML, TikTok, Amazon)

Eduardo, 25/09/2026: "o Duoke nosso" — conversas de todas as lojas numa fila
só, com a IA deixando a resposta pronta. Plano em docs/atendimento-unificado.md.

Tabelas PRÓPRIAS, não `dm_conversas`: o robô do Instagram varre aquelas e
responde sozinho; conversa de marketplace lá dentro seria respondida por quem
não conhece pedido nem as regras de cada loja.

  • `atendimento_canais`: uma por (integração × canal), com o MODO da loja.
    Toda loja nasce em `observar` (só lê) — esta migration não liga nada.
  • `atendimento_conversas` / `atendimento_mensagens`: o espelho do que está
    na plataforma. UNIQUE (conversa_id, externo_id) é a idempotência do sync
    NO BANCO: a mesma mensagem volta em toda rodada de consulta.
    `comprador_avatar` (28/09, tela "igual ao Duoke") é só a URL da foto que
    a API de chat entrega; o retrato do pedido e o cartão do anúncio ficam em
    `dados` (`pedido_mkt`, `produto`), sem coluna própria.
  • `atendimento_rascunhos` / `atendimento_avaliacoes`: o que a IA sugeriu e
    o que a pessoa fez com isso — o material do aprendizado.
  • `atendimento_regras` / `atendimento_modelos`: manual "QUANDO → FAÇA" e
    respostas prontas. Parte 2 (28/09): a regra ganha `tipo` (seguranca →
    categoria → estilo, a ordem no prompt), `categoria` (NULL = geral) e
    `prioridade`; a resposta pronta ganha `categoria`. Quem já existia vira
    `categoria` geral — o mesmo comportamento de antes.
  • Parte 2 (28/09): `atendimento_categorias` (a lista oficial de assuntos do
    manual base, com a descrição que a IA usa para classificar),
    `atendimento_pedidos_comprador` e `atendimento_avaliacoes_loja` (os
    índices do cartão "Cliente": a Shopee não filtra pedido por comprador).
    Esta revisão ainda não tinha ido para produção quando ganhou as três —
    por isso entram aqui, e não numa 0347.

Dois índices únicos PARCIAIS, declarados aqui E no model (como
`uq_dm_resposta_em_voo` na 0288):
  • `uq_atendimento_envio_em_voo`: uma resposta `enviando` por conversa — duas
    abas apertando "Enviar" não mandam duas mensagens ao cliente.
  • `uq_atendimento_rascunho_pendente`: um rascunho pendente por conversa.

Os nomes de constraint seguem a NAMING_CONVENTION de app/models/base.py; o
único nome à mão é o da FK do gatilho, que pela convenção passaria dos 63
caracteres do Postgres. `tests/test_atendimento_migration.py` roda esta
migration num schema de teste e confere, coluna por coluna, contra o model.

Revision ID: 0346_atendimento
Revises: 0345_denuncia_acesso_cairo
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0346_atendimento"
down_revision: str | None = "0345_denuncia_acesso_cairo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def _fk_user(tabela: str, coluna: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [coluna], [f"{SCHEMA}.users.id"],
        name=f"fk_{tabela}_{coluna}_users", ondelete="SET NULL",
    )


def upgrade() -> None:
    # As FKs pegam ShareRowExclusiveLock em `integrations` e `users` com a api
    # rodando. Sem teto, uma transação longa faria a migration esperar e
    # enfileirar todo mundo atrás dela (mesmo cuidado da 0288).
    op.execute("SET lock_timeout = '3s'")

    op.create_table(
        "atendimento_canais",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("integration_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("plataforma", sa.String(16), nullable=False),
        sa.Column("canal", sa.String(16), nullable=False),
        # Toda loja nasce só lendo. Assumir a resposta é decisão de pessoa.
        sa.Column("modo", sa.String(16), server_default=sa.text("'observar'"), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'novo'"), nullable=False),
        sa.Column("cursor", pg.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("nao_lidas_plataforma", sa.Integer(), nullable=True),
        sa.Column("ultimo_ok_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultimo_erro_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultimo_erro", sa.Text(), nullable=True),
        sa.Column(
            "auto_categorias", pg.JSONB(), server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["integration_id"], [f"{SCHEMA}.integrations.id"],
            name="fk_atendimento_canais_integration_id_integrations", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_atendimento_canais"),
        sa.UniqueConstraint(
            "integration_id", "canal", name="uq_atendimento_canais_integration_id_canal"
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "atendimento_conversas",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        # SET NULL nos dois: desligar a loja não apaga o que o cliente
        # escreveu — plataforma/canal/conta ficam em snapshot.
        sa.Column("canal_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("integration_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("plataforma", sa.String(16), nullable=False),
        sa.Column("canal", sa.String(16), nullable=False),
        sa.Column("conta", sa.Text(), nullable=True),
        sa.Column("externo_id", sa.String(191), nullable=False),
        sa.Column("comprador_id", sa.String(128), nullable=True),
        sa.Column("comprador_nome", sa.Text(), nullable=True),
        # Só a URL da foto que a API de chat entrega (Shopee/TikTok); ML e
        # Amazon não têm, e a tela mostra as iniciais.
        sa.Column("comprador_avatar", sa.Text(), nullable=True),
        sa.Column("pedido_marketplace", sa.String(64), nullable=True),
        sa.Column("anuncio_id", sa.String(64), nullable=True),
        sa.Column("anuncio_titulo", sa.Text(), nullable=True),
        sa.Column("ultima_mensagem_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultima_mensagem_resumo", sa.Text(), nullable=True),
        sa.Column("ultima_autor", sa.String(16), nullable=True),
        sa.Column("ultima_do_cliente_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultima_da_loja_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "aguardando_resposta", sa.Boolean(), server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("prazo_resposta_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("nao_lidas", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "situacao", sa.String(16), server_default=sa.text("'aberta'"), nullable=False
        ),
        sa.Column("bloqueio_motivo", sa.Text(), nullable=True),
        sa.Column("pode_enviar_ate", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "sem_resposta_necessaria", sa.Boolean(), server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("atribuido_a", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("ia_pausada", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("dados", pg.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["canal_id"], [f"{SCHEMA}.atendimento_canais.id"],
            name="fk_atendimento_conversas_canal_id_atendimento_canais", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["integration_id"], [f"{SCHEMA}.integrations.id"],
            name="fk_atendimento_conversas_integration_id_integrations", ondelete="SET NULL",
        ),
        _fk_user("atendimento_conversas", "atribuido_a"),
        sa.PrimaryKeyConstraint("id", name="pk_atendimento_conversas"),
        sa.UniqueConstraint(
            "integration_id", "canal", "externo_id",
            name="uq_atendimento_conversas_integration_id_canal_externo_id",
        ),
        schema=SCHEMA,
    )
    # A lista ordena por última mensagem e filtra por "aguardando" e
    # "vencendo"; o painel do pedido chega pela coluna do pedido.
    for coluna in (
        "pedido_marketplace", "ultima_mensagem_em", "aguardando_resposta", "prazo_resposta_em",
    ):
        op.create_index(
            f"ix_atendimento_conversas_{coluna}", "atendimento_conversas", [coluna],
            schema=SCHEMA,
        )

    op.create_table(
        "atendimento_mensagens",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("conversa_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("externo_id", sa.String(191), nullable=True),
        sa.Column("autor", sa.String(16), nullable=False),
        sa.Column("origem", sa.String(24), nullable=False),
        sa.Column("autor_user_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("tipo", sa.String(16), server_default=sa.text("'texto'"), nullable=False),
        sa.Column("texto", sa.Text(), nullable=True),
        sa.Column("anexos", pg.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        # Relógio da PLATAFORMA: é ele que conta o SLA.
        sa.Column("enviada_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status", sa.String(16), server_default=sa.text("'recebida'"), nullable=False
        ),
        sa.Column("erro", sa.Text(), nullable=True),
        # Sem FK de propósito: o rascunho já aponta para a mensagem gatilho, e
        # FK nos dois sentidos vira ciclo.
        sa.Column("rascunho_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("payload", pg.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["conversa_id"], [f"{SCHEMA}.atendimento_conversas.id"],
            name="fk_atendimento_mensagens_conversa_id_atendimento_conversas",
            ondelete="CASCADE",
        ),
        _fk_user("atendimento_mensagens", "autor_user_id"),
        sa.PrimaryKeyConstraint("id", name="pk_atendimento_mensagens"),
        # Nullable: a nossa resposta só ganha id depois de enviada, e o
        # Postgres deixa vários NULL conviverem no índice único.
        sa.UniqueConstraint(
            "conversa_id", "externo_id",
            name="uq_atendimento_mensagens_conversa_id_externo_id",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_atendimento_mensagens_conversa_id", "atendimento_mensagens", ["conversa_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_atendimento_mensagens_enviada_em", "atendimento_mensagens", ["enviada_em"],
        schema=SCHEMA,
    )
    # UMA resposta em voo por conversa. Mensagem enviada não se desfaz; a
    # trava é este índice, no banco, não a aplicação.
    op.create_index(
        "uq_atendimento_envio_em_voo",
        "atendimento_mensagens",
        ["conversa_id"],
        unique=True,
        postgresql_where=sa.text("status = 'enviando'"),
        schema=SCHEMA,
    )

    op.create_table(
        "atendimento_rascunhos",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("conversa_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("mensagem_gatilho_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("texto", sa.Text(), nullable=True),
        sa.Column("categoria", sa.String(32), nullable=True),
        sa.Column("confianca", sa.Float(), nullable=True),
        sa.Column(
            "precisa_humano", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column(
            "validador_ok", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "validador_erros", pg.JSONB(), server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("fatos", pg.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("modelo", sa.String(128), nullable=True),
        sa.Column("prompt_versao", sa.String(16), nullable=True),
        sa.Column("manual_hash", sa.String(16), nullable=True),
        sa.Column("tokens_entrada", sa.Integer(), nullable=True),
        sa.Column("tokens_saida", sa.Integer(), nullable=True),
        sa.Column(
            "status", sa.String(16), server_default=sa.text("'pendente'"), nullable=False
        ),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["conversa_id"], [f"{SCHEMA}.atendimento_conversas.id"],
            name="fk_atendimento_rascunhos_conversa_id_atendimento_conversas",
            ondelete="CASCADE",
        ),
        # Nome à mão: pela convenção passaria de 63 caracteres.
        sa.ForeignKeyConstraint(
            ["mensagem_gatilho_id"], [f"{SCHEMA}.atendimento_mensagens.id"],
            name="fk_atendimento_rascunhos_gatilho_atendimento_mensagens",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_atendimento_rascunhos"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_atendimento_rascunhos_conversa_id", "atendimento_rascunhos", ["conversa_id"],
        schema=SCHEMA,
    )
    # UM rascunho pendente por conversa: a tela mostra uma sugestão, e dois
    # workers gerando para a mesma conversa gastariam token à toa.
    op.create_index(
        "uq_atendimento_rascunho_pendente",
        "atendimento_rascunhos",
        ["conversa_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pendente'"),
        schema=SCHEMA,
    )

    op.create_table(
        "atendimento_avaliacoes",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("rascunho_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("acao", sa.String(24), nullable=False),
        sa.Column("texto_final", sa.Text(), nullable=True),
        sa.Column("similaridade", sa.Float(), nullable=True),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column("nota", sa.String(8), nullable=True),
        sa.Column("correcao", sa.Text(), nullable=True),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["rascunho_id"], [f"{SCHEMA}.atendimento_rascunhos.id"],
            name="fk_atendimento_avaliacoes_rascunho_id_atendimento_rascunhos",
            ondelete="CASCADE",
        ),
        _fk_user("atendimento_avaliacoes", "user_id"),
        sa.PrimaryKeyConstraint("id", name="pk_atendimento_avaliacoes"),
        # Uma avaliação por rascunho.
        sa.UniqueConstraint("rascunho_id", name="uq_atendimento_avaliacoes_rascunho_id"),
        schema=SCHEMA,
    )

    op.create_table(
        "atendimento_regras",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("quando", sa.Text(), nullable=False),
        sa.Column("faca", sa.Text(), nullable=False),
        # NULL = vale para todas.
        sa.Column("plataforma", sa.String(16), nullable=True),
        sa.Column("canal", sa.String(16), nullable=True),
        sa.Column("ativa", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("criado_por", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("atualizado_por", pg.UUID(as_uuid=True), nullable=True),
        # seguranca (sempre, primeiro) | categoria (só no assunto; NULL =
        # geral) | estilo (por último). Sem FK na categoria: a lista pode
        # estar só nas constantes.
        sa.Column("categoria", sa.String(32), nullable=True),
        sa.Column(
            "tipo", sa.String(16), server_default=sa.text("'categoria'"), nullable=False
        ),
        sa.Column("prioridade", sa.Integer(), server_default=sa.text("100"), nullable=False),
        *_timestamps(),
        _fk_user("atendimento_regras", "criado_por"),
        _fk_user("atendimento_regras", "atualizado_por"),
        sa.PrimaryKeyConstraint("id", name="pk_atendimento_regras"),
        schema=SCHEMA,
    )

    op.create_table(
        "atendimento_modelos",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("titulo", sa.Text(), nullable=False),
        sa.Column("texto", sa.Text(), nullable=False),
        sa.Column("plataforma", sa.String(16), nullable=True),
        sa.Column("canal", sa.String(16), nullable=True),
        sa.Column("ativo", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("ordem", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("criado_por", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("categoria", sa.String(32), nullable=True),
        *_timestamps(),
        _fk_user("atendimento_modelos", "criado_por"),
        sa.PrimaryKeyConstraint("id", name="pk_atendimento_modelos"),
        schema=SCHEMA,
    )

    # ── Parte 2 ──────────────────────────────────────────────────────────
    # A lista oficial de assuntos (manual base). Vazia = constantes.CATEGORIAS.
    op.create_table(
        "atendimento_categorias",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("descricao", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("exemplos", pg.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("so_humano", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("lacunas", pg.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("ativa", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("ordem", sa.Integer(), server_default=sa.text("100"), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_atendimento_categorias"),
        schema=SCHEMA,
    )

    # Pedido × comprador por loja: a Shopee não filtra pedido por comprador.
    # `comprador_id` é o id da plataforma, não dado pessoal.
    op.create_table(
        "atendimento_pedidos_comprador",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("integration_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("plataforma", sa.String(16), nullable=False),
        sa.Column("comprador_id", sa.String(128), nullable=False),
        sa.Column("pedido", sa.String(64), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total", sa.Float(), nullable=True),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("itens_resumo", sa.Text(), nullable=True),
        sa.Column(
            "atualizado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["integration_id"], [f"{SCHEMA}.integrations.id"],
            name="fk_atendimento_pedidos_comprador_integration_id_integrations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_atendimento_pedidos_comprador"),
        sa.UniqueConstraint(
            "integration_id", "pedido",
            name="uq_atendimento_pedidos_comprador_integration_id_pedido",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_atendimento_pedidos_comprador_integration_id_comprador_id",
        "atendimento_pedidos_comprador",
        ["integration_id", "comprador_id"],
        schema=SCHEMA,
    )

    # Avaliações da loja (Shopee get_comment), casadas pelo pedido e usuário.
    op.create_table(
        "atendimento_avaliacoes_loja",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("integration_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("plataforma", sa.String(16), nullable=False),
        sa.Column("comentario_id", sa.String(64), nullable=False),
        sa.Column("pedido", sa.String(64), nullable=True),
        sa.Column("comprador_nome_loja", sa.Text(), nullable=True),
        sa.Column("item_id", sa.String(64), nullable=True),
        sa.Column("estrelas", sa.SmallInteger(), nullable=False),
        sa.Column("texto", sa.Text(), nullable=True),
        sa.Column("resposta_loja", sa.Text(), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["integration_id"], [f"{SCHEMA}.integrations.id"],
            name="fk_atendimento_avaliacoes_loja_integration_id_integrations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_atendimento_avaliacoes_loja"),
        sa.UniqueConstraint(
            "integration_id", "comentario_id",
            name="uq_atendimento_avaliacoes_loja_integration_id_comentario_id",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_atendimento_avaliacoes_loja_integration_id_pedido",
        "atendimento_avaliacoes_loja",
        ["integration_id", "pedido"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    # Filhas antes das mães. DROP TABLE leva junto os índices e constraints.
    for tabela in (
        "atendimento_avaliacoes_loja",
        "atendimento_pedidos_comprador",
        "atendimento_categorias",
        "atendimento_modelos",
        "atendimento_regras",
        "atendimento_avaliacoes",
        "atendimento_rascunhos",
        "atendimento_mensagens",
        "atendimento_conversas",
        "atendimento_canais",
    ):
        op.drop_table(tabela, schema=SCHEMA)
