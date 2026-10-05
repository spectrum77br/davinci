"""atendimento: mensagens automáticas (regras por loja e o registro), começando em modo seco

Eduardo, 05/10/2026: "fazer uma pesquisa ali no Duoke pra saber as mensagens
automáticas que são usadas lá hoje ... pra aplicarmos aí, daí tal dia manda
tal mensagem". Combinado: o DaVinci recria as automações do Duoke COMEÇANDO
EM MODO SECO — registra o que mandaria, para quem e quando, e não envia nada —
para comparar com o que o Duoke manda de verdade; depois, loja por loja,
desliga no Duoke e liga no DaVinci. Desenho em docs/atendimento-automacoes.md.

O que muda (só aditivo):
  • `atendimento_automacao_regras`: automação × loja — modo (desligado,
    simular, enviar), partes do texto, atraso, horário, condições, versão,
    o disjuntor ("Duoke ainda ligado?") e quem mudou. UNIQUE (automacao,
    integration_id). Fica COM o gatilho do Histórico (quem mudou o quê);
  • `atendimento_automacao_registros`: uma linha por alvo × automação, com a
    chave única (a trava contra duplicar), o estado, o motivo e a comparação
    com o Duoke. Sem texto nenhum. Fica FORA do Histórico (máquina).

SEMENTE: cada loja Shopee, TikTok e Mercado Livre ganha as regras do catálogo
(`services/atendimento/automacoes_catalogo.py`) com os textos padrão — os do
Duoke, em português do Brasil e com o nome do comprador dentro do texto — em
`simular` nas lojas onde o Duoke manda hoje (levantamento de 05/10) e em
`desligado` no resto. A loja casa pelo nome da integração (strip + lower).
Nada roda até `ATENDIMENTO_AUTOMACOES_ATIVA=true`, e nada sai sem
`ATENDIMENTO_AUTOMACOES_ENVIO=true`: com a regra em `simular`, o motor só
registra.

`lock_timeout` de 3 s, como as outras do atendimento (as FKs novas pegam
ShareRowExclusiveLock em integrations, users, conversas e mensagens).
`tests/test_atendimento_migration.py` roda esta num schema descartável e
compara com o model.

O downgrade apaga as duas tabelas (o registro e as regras se perdem).

Revision ID: 0366_atendimento_automacoes
Revises: 0365_marca_emails
"""

import json
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0366_atendimento_automacoes"
down_revision: str | None = "0365_marca_emails"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
REGRAS = "atendimento_automacao_regras"
REGISTROS = "atendimento_automacao_registros"
# As automações que ESTA migration semeia — congeladas como lista (05/10/2026,
# à noite): o catálogo ganhou automações depois dela (o pedido não pago, a
# resposta da avaliação e o "pedido recebido" do TikTok), e quem as semeia é a
# 0371. Assim a 0366 semeia num banco novo exatamente o que semeou em produção.
CODIGOS = (
    "shopee_menu",
    *(f"shopee_opcao_{n}" for n in range(1, 7)),
    "shopee_aguarde",
    "shopee_convite",
    "shopee_duvida_2h",
    "shopee_duvida_26h",
    "shopee_pedido_recebido",
    "shopee_entregue",
    "shopee_pos_conclusao",
    "tiktok_aguarde",
    "tiktok_convite",
    "tiktok_duvida_2h",
    "tiktok_duvida_24h",
    "ml_menu",
    *(f"ml_opcao_{n}" for n in range(1, 7)),
)


def _uuid(nome: str, nullable: bool = True) -> sa.Column:
    return sa.Column(nome, pg.UUID(as_uuid=True), nullable=nullable)


def _jsonb(nome: str, vazio: str) -> sa.Column:
    return sa.Column(nome, pg.JSONB(), server_default=sa.text(f"'{vazio}'::jsonb"), nullable=False)


def _quando(nome: str, nullable: bool = True, agora: bool = False) -> sa.Column:
    return sa.Column(
        nome,
        sa.DateTime(timezone=True),
        server_default=sa.text("now()") if agora else None,
        nullable=nullable,
    )


def _carimbos() -> list[sa.Column]:
    return [
        _quando("created_at", nullable=False, agora=True),
        _quando("updated_at", nullable=False, agora=True),
    ]


def _semear() -> int:
    """As regras do catálogo em cada loja Shopee/TikTok/ML ativa; devolve quantas."""
    from app.services.atendimento import automacoes_catalogo as cat

    conn = op.get_bind()
    lojas = conn.execute(
        sa.text(
            f'SELECT id, platform::text, name FROM "{SCHEMA}".integrations '  # noqa: S608
            "WHERE archived_at IS NULL AND platform::text IN ('shopee', 'tiktok', 'ml')"
        )
    ).all()
    inserir = sa.text(
        f'INSERT INTO "{SCHEMA}".{REGRAS} '  # noqa: S608 — nomes fixos daqui
        "(id, automacao, integration_id, plataforma, modo, ligada_desde, partes, atraso_min, "
        "janela_inicio, janela_fim, condicoes) VALUES (gen_random_uuid(), :automacao, :integ, "
        ":plataforma, :modo, :ligada, "
        "CAST(:partes AS jsonb), :atraso, :ini, :fim, CAST(:condicoes AS jsonb)) "
        "ON CONFLICT (automacao, integration_id) DO NOTHING"
    )
    agora = datetime.now(UTC)
    n = 0
    for integ, plataforma, nome in lojas:
        for aut in cat.por_plataforma(cat.plataforma_da_integracao(plataforma)):
            if aut.codigo not in CODIGOS:
                continue  # é da 0371 (veja `CODIGOS`)
            r = cat.regra_semente(aut, nome)
            conn.execute(
                inserir,
                {
                    "automacao": r["automacao"],
                    "integ": integ,
                    "plataforma": r["plataforma"],
                    "modo": r["modo"],
                    "ligada": None if r["modo"] == cat.MODO_DESLIGADO else agora,
                    "partes": json.dumps(r["partes"], ensure_ascii=False),
                    "atraso": r["atraso_min"],
                    "ini": r["janela_inicio"],
                    "fim": r["janela_fim"],
                    "condicoes": json.dumps(r["condicoes"], ensure_ascii=False),
                },
            )
            n += 1
    return n


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")

    op.create_table(
        REGRAS,
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("automacao", sa.String(48), nullable=False),
        _uuid("integration_id", nullable=False),
        sa.Column("plataforma", sa.String(16), nullable=False),
        sa.Column("modo", sa.String(16), server_default=sa.text("'desligado'"), nullable=False),
        _quando("ligada_desde"),
        _quando("enviar_desde"),
        _jsonb("partes", "[]"),
        sa.Column("atraso_min", sa.Integer(), nullable=False),
        sa.Column("janela_inicio", sa.Time(), nullable=True),
        sa.Column("janela_fim", sa.Time(), nullable=True),
        _jsonb("condicoes", "{}"),
        sa.Column("teto_dia", sa.Integer(), nullable=True),
        sa.Column("versao", sa.Integer(), server_default=sa.text("1"), nullable=False),
        _quando("disjuntor_em"),
        sa.Column("disjuntor_motivo", sa.String(48), nullable=True),
        _uuid("atualizado_por"),
        *_carimbos(),
        sa.CheckConstraint(
            "modo IN ('desligado', 'simular', 'enviar')",
            # Nome final (`op.f`): sem passar de novo pela convenção ck_<tabela>_<nome>.
            name=op.f("ck_atendimento_automacao_regras_modo"),
        ),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            [f"{SCHEMA}.integrations.id"],
            name="fk_atendimento_automacao_regras_integration_id_integrations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["atualizado_por"],
            [f"{SCHEMA}.users.id"],
            name="fk_atendimento_automacao_regras_atualizado_por_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_atendimento_automacao_regras"),
        sa.UniqueConstraint(
            "automacao",
            "integration_id",
            name="uq_atendimento_automacao_regras_automacao_integration_id",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        REGISTROS,
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("automacao", sa.String(48), nullable=False),
        _uuid("regra_id"),
        sa.Column("regra_versao", sa.Integer(), nullable=True),
        _uuid("integration_id", nullable=False),
        sa.Column("plataforma", sa.String(16), nullable=False),
        sa.Column("alvo", sa.String(16), nullable=False),
        sa.Column("chave", sa.String(191), nullable=False),
        _uuid("conversa_id"),
        _uuid("gatilho_mensagem_id"),
        sa.Column("pedido", sa.String(64), nullable=True),
        sa.Column("comprador_id", sa.String(128), nullable=True),
        _quando("evento_em"),
        _quando("visto_em", nullable=False, agora=True),
        _quando("devido_em", nullable=False),
        _quando("decidido_em"),
        sa.Column("estado", sa.String(16), server_default=sa.text("'agendado'"), nullable=False),
        sa.Column("modo", sa.String(16), nullable=True),
        sa.Column("motivo", sa.String(48), nullable=True),
        sa.Column("erro", sa.Text(), nullable=True),
        _jsonb("mensagem_ids", "[]"),
        sa.Column("tentativas", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("duoke", sa.String(16), nullable=True),
        _uuid("duoke_mensagem_id"),
        _quando("duoke_em"),
        sa.Column("duoke_diferenca_s", sa.Integer(), nullable=True),
        sa.Column("divergencia", sa.String(32), nullable=True),
        sa.Column("alerta", sa.String(32), nullable=True),
        _quando("comparado_em"),
        *_carimbos(),
        sa.CheckConstraint(
            "estado IN ('agendado', 'simulado', 'enviando', 'enviado', 'pulado',"
            " 'falhou', 'revisar', 'so_duoke')",
            name=op.f("ck_atendimento_automacao_registros_estado"),
        ),
        # Nomes à mão: pela convenção passariam dos 63 caracteres do Postgres.
        sa.ForeignKeyConstraint(
            ["regra_id"],
            [f"{SCHEMA}.{REGRAS}.id"],
            name="fk_atendimento_automacao_registros_regra",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            [f"{SCHEMA}.integrations.id"],
            name="fk_atendimento_automacao_registros_integration_id_integrations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversa_id"],
            [f"{SCHEMA}.atendimento_conversas.id"],
            name="fk_atendimento_automacao_registros_conversa",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["gatilho_mensagem_id"],
            [f"{SCHEMA}.atendimento_mensagens.id"],
            name="fk_atendimento_automacao_registros_gatilho",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["duoke_mensagem_id"],
            [f"{SCHEMA}.atendimento_mensagens.id"],
            name="fk_atendimento_automacao_registros_duoke",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_atendimento_automacao_registros"),
        sa.UniqueConstraint(
            "automacao",
            "integration_id",
            "chave",
            name="uq_atendimento_automacao_registros_chave",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_atendimento_automacao_registros_tela",
        REGISTROS,
        ["integration_id", "automacao", "devido_em"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_atendimento_automacao_registros_conversa",
        REGISTROS,
        ["conversa_id", "automacao", "devido_em"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_atendimento_automacao_registros_pedido",
        REGISTROS,
        ["integration_id", "pedido"],
        postgresql_where=sa.text("pedido IS NOT NULL"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_atendimento_automacao_registros_agendados",
        REGISTROS,
        ["devido_em"],
        postgresql_where=sa.text("estado = 'agendado'"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_atendimento_automacao_registros_comparar",
        REGISTROS,
        ["devido_em"],
        postgresql_where=sa.text("duoke = 'pendente'"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_atendimento_automacao_registros_duoke",
        REGISTROS,
        ["duoke_mensagem_id"],
        unique=True,
        postgresql_where=sa.text("duoke_mensagem_id IS NOT NULL"),
        schema=SCHEMA,
    )

    _semear()


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    # DROP TABLE leva junto os índices e as FKs.
    op.drop_table(REGISTROS, schema=SCHEMA)
    op.drop_table(REGRAS, schema=SCHEMA)
