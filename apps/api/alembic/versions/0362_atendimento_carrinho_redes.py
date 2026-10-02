"""atendimento: canal externo (sites e redes), carrinho abandonado, publicações e comentários

Eduardo, 02/10/2026 (PROJETO-COMUNICADOR.md, RF9 e RF7; levantamento em
docs/atendimento-comunicador-levantamento.md §2.5, §3.11 e §3.13):

  • CARRINHO ABANDONADO dos sites Charlots e Uranyx (PHP próprio na
    Hostinger): o lojista logado tem o carrinho no banco do site; o DaVinci o
    lê servidor a servidor (`services/atendimento/carrinhos.py`) e abre a
    conversa `carrinho` com a etiqueta CARRINHO;
  • COMENTÁRIOS E MENÇÕES do Instagram e da Página do Facebook da Charlots e
    da Uranyx (`services/atendimento/redes.py`): uma conversa `comentario`
    por (pessoa, publicação), etiqueta MÍDIA.

Nenhum dos dois tem integração de marketplace nem robô do Mac mini: o canal
ganha uma ORIGEM EXTERNA genérica.

O que muda (só aditivo):
  • `atendimento_canais.externo_ref` (String 191): "site:<site>",
    "rede:instagram:<ig_user_id>", "rede:facebook:<page_id>"; UNIQUE
    (externo_ref, canal);
  • `atendimento_canais.rede_social_id` (FK `redes_sociais`, SET NULL): a
    conta do cadastro — é por ela que a barra de lojas junta o Direct e os
    comentários da mesma conta;
  • o CHECK `ck_atendimento_canais_integracao_ou_robo` (0347) vira
    `ck_atendimento_canais_tem_origem`: integração OU robô OU origem externa
    (o canal que já existe passa nas duas regras);
  • `atendimento_carrinhos` (o episódio do carrinho parado, com o índice
    único PARCIAL "um aberto por lojista por site"),
    `atendimento_publicacoes` e `atendimento_comentarios`.

A conversa do canal externo usa o índice único parcial que já existe
(`uq_atendimento_conversas_robo`, 0347: (canal_id, externo_id) sem
integração e com canal) — vale para qualquer canal sem integração.

`lock_timeout` de 3 s, como a 0347/0353/0358: o ALTER em
`atendimento_canais` pega AccessExclusiveLock com o sync e a api rodando.
`tests/test_atendimento_migration.py` roda 0346 + 0347 + 0353 + 0358 + esta
num schema descartável e compara o catálogo com o model.

O downgrade apaga as três tabelas (o que foi lido se perde) e os canais
externos (as conversas ficam, com `canal_id` NULL — como a de uma loja
desconectada), e volta o CHECK de antes.

Revision ID: 0362_atendimento_carrinho_redes
Revises: 0361_denuncia_robo_agenda
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0362_atendimento_carrinho_redes"
down_revision: str | None = "0361_denuncia_robo_agenda"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
CANAIS = "atendimento_canais"
CHECK_ANTIGO = "ck_atendimento_canais_integracao_ou_robo"
CHECK_NOVO = "ck_atendimento_canais_tem_origem"


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


def upgrade() -> None:
    # ALTER TABLE pega AccessExclusiveLock no canal (e as FKs novas pegam
    # ShareRowExclusiveLock em conversas, users e redes_sociais) com a api,
    # o sync e os crons rodando: sem teto, uma transação longa faria a
    # migration esperar e enfileirar todo mundo atrás dela.
    op.execute("SET lock_timeout = '3s'")

    # ── O canal externo ──────────────────────────────────────────────────
    op.add_column(CANAIS, sa.Column("externo_ref", sa.String(191), nullable=True), schema=SCHEMA)
    op.add_column(CANAIS, _uuid("rede_social_id"), schema=SCHEMA)
    op.create_foreign_key(
        "fk_atendimento_canais_rede_social_id_redes_sociais",
        CANAIS,
        "redes_sociais",
        ["rede_social_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_atendimento_canais_externo_ref_canal",
        CANAIS,
        ["externo_ref", "canal"],
        schema=SCHEMA,
    )
    # Nome final (convenção ck_<tabela>_<nome>), sem passar de novo por ela.
    op.drop_constraint(op.f(CHECK_ANTIGO), CANAIS, type_="check", schema=SCHEMA)
    op.create_check_constraint(
        op.f(CHECK_NOVO),
        CANAIS,
        "integration_id IS NOT NULL OR robo_perfil_id IS NOT NULL OR externo_ref IS NOT NULL",
        schema=SCHEMA,
    )

    # ── Carrinho abandonado (RF9) ────────────────────────────────────────
    op.create_table(
        "atendimento_carrinhos",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("site", sa.String(32), nullable=False),
        _uuid("canal_id"),
        _uuid("conversa_id"),
        sa.Column("lojista_id", sa.String(64), nullable=False),
        _jsonb("lojista", "{}"),
        _jsonb("itens", "[]"),
        sa.Column("quantidade_total", sa.Integer(), server_default=sa.text("0"), nullable=False),
        _quando("parado_desde", nullable=False),
        _quando("detectado_em", nullable=False, agora=True),
        _quando("visto_em"),
        sa.Column("situacao", sa.String(16), server_default=sa.text("'aberto'"), nullable=False),
        _quando("encerrado_em"),
        sa.Column("motivo_fim", sa.String(32), nullable=True),
        _quando("recuperado_em"),
        _uuid("tratado_por"),
        _quando("tratado_em"),
        _jsonb("dados", "{}"),
        *_carimbos(),
        sa.ForeignKeyConstraint(
            ["canal_id"],
            [f"{SCHEMA}.atendimento_canais.id"],
            name="fk_atendimento_carrinhos_canal_id_atendimento_canais",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["conversa_id"],
            [f"{SCHEMA}.atendimento_conversas.id"],
            name="fk_atendimento_carrinhos_conversa_id_atendimento_conversas",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tratado_por"],
            [f"{SCHEMA}.users.id"],
            name="fk_atendimento_carrinhos_tratado_por_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_atendimento_carrinhos"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_atendimento_carrinhos_conversa_id",
        "atendimento_carrinhos",
        ["conversa_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_atendimento_carrinhos_site_lojista_id",
        "atendimento_carrinhos",
        ["site", "lojista_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "uq_atendimento_carrinhos_aberto",
        "atendimento_carrinhos",
        ["site", "lojista_id"],
        unique=True,
        postgresql_where=sa.text("situacao = 'aberto'"),
        schema=SCHEMA,
    )

    # ── Publicações e comentários das redes (RF7) ────────────────────────
    op.create_table(
        "atendimento_publicacoes",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        _uuid("canal_id"),
        sa.Column("plataforma", sa.String(16), nullable=False),
        sa.Column("conta_id", sa.String(64), nullable=False),
        sa.Column("externo_id", sa.String(128), nullable=False),
        sa.Column("tipo", sa.String(16), server_default=sa.text("'propria'"), nullable=False),
        sa.Column("formato", sa.String(24), nullable=True),
        sa.Column("autor_username", sa.Text(), nullable=True),
        sa.Column("legenda", sa.Text(), nullable=True),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("miniatura_url", sa.Text(), nullable=True),
        _quando("miniatura_lida_em"),
        _quando("publicada_em"),
        sa.Column("curtidas", sa.Integer(), nullable=True),
        sa.Column("comentarios", sa.Integer(), nullable=True),
        _jsonb("dados", "{}"),
        *_carimbos(),
        sa.ForeignKeyConstraint(
            ["canal_id"],
            [f"{SCHEMA}.atendimento_canais.id"],
            name="fk_atendimento_publicacoes_canal_id_atendimento_canais",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_atendimento_publicacoes"),
        sa.UniqueConstraint(
            "plataforma",
            "conta_id",
            "externo_id",
            name="uq_atendimento_publicacoes_plataforma_conta_id_externo_id",
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "atendimento_comentarios",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        _uuid("publicacao_id", nullable=False),
        _uuid("conversa_id"),
        sa.Column("plataforma", sa.String(16), nullable=False),
        sa.Column("externo_id", sa.String(128), nullable=False),
        sa.Column("pai_externo_id", sa.String(128), nullable=True),
        sa.Column("autor_id", sa.String(128), nullable=True),
        sa.Column("autor_username", sa.Text(), nullable=True),
        sa.Column("da_marca", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("texto", sa.Text(), nullable=True),
        _quando("criado_em"),
        sa.Column("curtidas", sa.Integer(), nullable=True),
        sa.Column("oculto", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("eh_pergunta", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        _jsonb("dados", "{}"),
        *_carimbos(),
        # Nome à mão: pela convenção passaria dos 63 caracteres do Postgres.
        sa.ForeignKeyConstraint(
            ["publicacao_id"],
            [f"{SCHEMA}.atendimento_publicacoes.id"],
            name="fk_atendimento_comentarios_publicacao",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversa_id"],
            [f"{SCHEMA}.atendimento_conversas.id"],
            name="fk_atendimento_comentarios_conversa_id_atendimento_conversas",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_atendimento_comentarios"),
        sa.UniqueConstraint(
            "plataforma", "externo_id", name="uq_atendimento_comentarios_plataforma_externo_id"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_atendimento_comentarios_conversa_id",
        "atendimento_comentarios",
        ["conversa_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_atendimento_comentarios_publicacao_id_criado_em",
        "atendimento_comentarios",
        ["publicacao_id", "criado_em"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    # DROP TABLE leva junto os índices e as FKs das tabelas.
    op.drop_table("atendimento_comentarios", schema=SCHEMA)
    op.drop_table("atendimento_publicacoes", schema=SCHEMA)
    op.drop_table("atendimento_carrinhos", schema=SCHEMA)
    # Os canais externos não têm integração nem robô: sem eles não dá para
    # voltar ao CHECK de antes. As conversas ficam (canal_id vira NULL pela
    # FK SET NULL).
    op.execute(
        f'DELETE FROM "{SCHEMA}".{CANAIS} '  # noqa: S608 — nomes fixos daqui
        "WHERE integration_id IS NULL AND robo_perfil_id IS NULL"
    )
    op.drop_constraint(op.f(CHECK_NOVO), CANAIS, type_="check", schema=SCHEMA)
    op.create_check_constraint(
        op.f(CHECK_ANTIGO),
        CANAIS,
        "integration_id IS NOT NULL OR robo_perfil_id IS NOT NULL",
        schema=SCHEMA,
    )
    op.drop_constraint(
        "uq_atendimento_canais_externo_ref_canal", CANAIS, type_="unique", schema=SCHEMA
    )
    # DROP COLUMN leva junto a FK da coluna.
    op.drop_column(CANAIS, "rede_social_id", schema=SCHEMA)
    op.drop_column(CANAIS, "externo_ref", schema=SCHEMA)
