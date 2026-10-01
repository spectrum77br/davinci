"""atendimento: etiqueta = status atual, histórico da etiqueta e reclamações da plataforma

Eduardo, 01/10/2026 (PROJETO-COMUNICADOR.md, RF1 e RF2; levantamento em
docs/atendimento-comunicador-levantamento.md §3.3 e §3.16): cada conversa tem
UMA etiqueta, que é o status atual dela e muda sozinha (PÓS-VENDA →
RECLAMAÇÃO quando o comprador abre uma reclamação), com a mudança na linha do
tempo; e as reclamações/mediações/devoluções das plataformas entram no
atendimento (o pedido 297840, ML 2000018509205724, tinha a reclamação
5582543195 invisível). O contrato de nomes com o outro dev (itens 4, 7 e 8)
chamava esta revisão de `0348_atendimento_etiquetas`; o `origin` ganhou a
0348–0351 (denúncia) e a 0352 (NF urgente) antes, então ela é a 0353.

O que muda (só aditivo: nenhuma coluna ou linha existente é alterada):
  • `atendimento_conversas`: `etiqueta`, `etiqueta_desde`, `etiqueta_manual`,
    `etiquetas_secundarias` (jsonb, []) e `etiqueta_automatica` (o que o motor
    calculou da última vez: é por ela que a troca à mão sabe que "aconteceu
    algo" e volta a valer o automático). Índice em `etiqueta` (filtro e
    contagem por etiqueta). Tudo NULL/false/[] nas conversas que já existem:
    quem preenche é o motor (`services/atendimento/etiqueta`), não esta
    migration.
  • `atendimento_etiquetas_historico`: uma linha por mudança (de, para,
    motivo, por_user_id — NULL = sistema —, em).
  • `atendimento_reclamacoes`: UNIQUE (plataforma, externo_id); índices por
    conversa, por pedido e por (status, prazo).
  • `bling_orders.numeroloja` ganha índice: a conversa casa com o pedido do
    Bling por `numero` OU `numeroloja` (o painel, e agora o motor da
    etiqueta a cada recálculo). Sem ele, cada busca era uma varredura das
    ~100 mil linhas do espelho (246 ms medidos em produção em 01/10). CREATE
    INDEX comum (o `autocommit_block` do alembic não funciona com o env
    assíncrono daqui — ver a 0331): segura a escrita em `bling_orders` pelo
    tempo de montar o índice (fração de segundo); `IF NOT EXISTS` para não
    brigar com um índice criado à mão.
  • `atendimento_mensagens.autor = 'mediador'` e `tipo = 'nota'` (origem
    `davinci_nota`): as colunas são texto livre, sem CHECK nem enum — nada a
    mudar no banco; o vocabulário está em `services/atendimento/constantes.py`.

Nomes de constraint pela NAMING_CONVENTION de app/models/base.py; o único à
mão é a FK do histórico para a conversa, que pela convenção passaria dos 63
caracteres do Postgres. `tests/test_atendimento_migration.py` roda 0346 +
0347 + esta num schema descartável e compara o catálogo com o model.

O downgrade APAGA as duas tabelas, as colunas da etiqueta e o índice de
`numeroloja` (o histórico e as reclamações lidas se perdem; a etiqueta se
recalcula).

Revision ID: 0353_atendimento_etiquetas
Revises: 0352_nf_command_urgente
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0353_atendimento_etiquetas"
down_revision: str | None = "0352_nf_command_urgente"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

_COLUNAS_ETIQUETA = (
    "etiqueta",
    "etiqueta_desde",
    "etiqueta_manual",
    "etiquetas_secundarias",
    "etiqueta_automatica",
)


def upgrade() -> None:
    # ALTER TABLE pega AccessExclusiveLock em `atendimento_conversas` e as FKs
    # novas pegam ShareRowExclusiveLock nela, em `integrations` e em `users`
    # com a api e o sync rodando: sem teto, uma transação longa (a rodada do
    # sync) faria a migration esperar e enfileirar todo mundo atrás dela
    # (mesmo cuidado da 0346/0347). ADD COLUMN com default constante não
    # reescreve a tabela (Postgres 11+).
    op.execute("SET lock_timeout = '3s'")

    op.add_column(
        "atendimento_conversas",
        sa.Column("etiqueta", sa.String(24), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "atendimento_conversas",
        sa.Column("etiqueta_desde", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "atendimento_conversas",
        sa.Column(
            "etiqueta_manual", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "atendimento_conversas",
        sa.Column(
            "etiquetas_secundarias", pg.JSONB(), server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "atendimento_conversas",
        sa.Column("etiqueta_automatica", sa.String(24), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_atendimento_conversas_etiqueta", "atendimento_conversas", ["etiqueta"],
        schema=SCHEMA,
    )

    op.create_table(
        "atendimento_etiquetas_historico",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("conversa_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("de", sa.String(24), nullable=True),
        sa.Column("para", sa.String(24), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column("por_user_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        # Nome à mão: pela convenção passaria de 63 caracteres.
        sa.ForeignKeyConstraint(
            ["conversa_id"], [f"{SCHEMA}.atendimento_conversas.id"],
            name="fk_atendimento_etiquetas_historico_conversa", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["por_user_id"], [f"{SCHEMA}.users.id"],
            name="fk_atendimento_etiquetas_historico_por_user_id_users", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_atendimento_etiquetas_historico"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_atendimento_etiquetas_historico_conversa_id_em",
        "atendimento_etiquetas_historico",
        ["conversa_id", "em"],
        schema=SCHEMA,
    )

    op.create_table(
        "atendimento_reclamacoes",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("integration_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("conversa_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("plataforma", sa.String(16), nullable=False),
        sa.Column("externo_id", sa.String(64), nullable=False),
        sa.Column("tipo", sa.String(16), nullable=False),
        sa.Column("status", sa.String(64), nullable=True),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column("pedido_marketplace", sa.String(64), nullable=True),
        sa.Column("prazo_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("aberta_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("encerrada_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dados", pg.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["integration_id"], [f"{SCHEMA}.integrations.id"],
            name="fk_atendimento_reclamacoes_integration_id_integrations", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["conversa_id"], [f"{SCHEMA}.atendimento_conversas.id"],
            name="fk_atendimento_reclamacoes_conversa_id_atendimento_conversas",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_atendimento_reclamacoes"),
        sa.UniqueConstraint(
            "plataforma", "externo_id", name="uq_atendimento_reclamacoes_plataforma_externo_id"
        ),
        schema=SCHEMA,
    )
    for coluna in ("conversa_id", "pedido_marketplace"):
        op.create_index(
            f"ix_atendimento_reclamacoes_{coluna}", "atendimento_reclamacoes", [coluna],
            schema=SCHEMA,
        )
    op.create_index(
        "ix_atendimento_reclamacoes_status_prazo_em",
        "atendimento_reclamacoes",
        ["status", "prazo_em"],
        schema=SCHEMA,
    )

    op.create_index(
        "ix_bling_orders_numeroloja", "bling_orders", ["numeroloja"], schema=SCHEMA,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.drop_index(
        "ix_bling_orders_numeroloja", table_name="bling_orders", schema=SCHEMA, if_exists=True
    )
    # Filhas antes da mãe. DROP TABLE leva junto os índices e constraints.
    op.drop_table("atendimento_reclamacoes", schema=SCHEMA)
    op.drop_table("atendimento_etiquetas_historico", schema=SCHEMA)
    op.drop_index(
        "ix_atendimento_conversas_etiqueta", table_name="atendimento_conversas", schema=SCHEMA
    )
    for coluna in reversed(_COLUNAS_ETIQUETA):
        op.drop_column("atendimento_conversas", coluna, schema=SCHEMA)
