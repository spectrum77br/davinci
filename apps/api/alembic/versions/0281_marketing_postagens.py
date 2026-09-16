"""marketing_postagens + redes_sociais_tokens — robô de postagem dos criativos

Eduardo, 15/09/2026: "um robô que fará a postagem desses vídeos do criativo
automaticamente; também teremos a opção de agendar uma data para a postagem
automática do criativo". Liga Marketing › Criativos a Cadastros › Redes
Sociais (migration 0277).

Quatro mudanças:
  • `marketing_postagens`: agenda E outbox na mesma linha (mesmo ciclo do
    logistica_robo_comando). Índice único PARCIAL enquanto a postagem está
    "em voo" — publicar não tem desfazer, então post duplicado é barrado no
    banco, não só no código.
  • `redes_sociais_tokens`: credencial de publicação por conta, cifrada
    (cipher.encrypt_json, BYTEA), com `token_expires_at` em claro pro cron de
    renovação achar o que vence sem decifrar nada.
  • `marketing_creatives.marca_id` + BACKFILL por lower(marca) = marcas.slug:
    é de onde o modal tira as contas da marca. A coluna `marca` (texto) fica.
  • `redes_sociais.postagem_auto` + tetos por conta (`postagem_max_dia`,
    `postagem_intervalo_min`, NULL = padrão do servidor).
  • `marketing_creative_files.sha256`: idempotência e trava de reusar o mesmo
    vídeo em marcas diferentes (Instagram e TikTok punem conteúdo repetido
    entre contas em silêncio). Arquivos antigos ficam NULL — o serviço só
    trava quando os dois lados têm hash.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0281_marketing_postagens"
down_revision: str | None = "0280_vw_perfis_grupo_marrocos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "marketing_postagens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "creative_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.marketing_creatives.id", ondelete="CASCADE",
                name="fk_marketing_postagens_creative_id_marketing_creatives",
            ),
            nullable=False,
        ),
        sa.Column(
            "file_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.marketing_creative_files.id", ondelete="CASCADE",
                name="fk_marketing_postagens_file_id_marketing_creative_files",
            ),
            nullable=False,
        ),
        sa.Column(
            "rede_social_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.redes_sociais.id", ondelete="SET NULL",
                name="fk_marketing_postagens_rede_social_id_redes_sociais",
            ),
            nullable=True,
        ),
        sa.Column("plataforma", sa.String(length=32), nullable=False),
        sa.Column("conta", sa.Text(), nullable=True),
        sa.Column("legenda", sa.Text(), nullable=True),
        sa.Column(
            "opcoes", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("agendado_para", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default=sa.text("'agendado'")
        ),
        sa.Column(
            "origem", sa.String(length=16), nullable=False, server_default=sa.text("'manual'")
        ),
        sa.Column(
            "executor", sa.String(length=16), nullable=False, server_default=sa.text("'api'")
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("tentativas_max", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("container_id", sa.String(length=128), nullable=True),
        sa.Column("post_external_id", sa.String(length=128), nullable=True),
        sa.Column("post_url", sa.Text(), nullable=True),
        sa.Column("publicado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.users.id", ondelete="SET NULL",
                name="fk_marketing_postagens_created_by_users",
            ),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_marketing_postagens_creative_id", "marketing_postagens", ["creative_id"], schema=SCHEMA
    )
    op.create_index(
        "ix_marketing_postagens_rede_social_id",
        "marketing_postagens",
        ["rede_social_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_marketing_postagens_agendado_para",
        "marketing_postagens",
        ["agendado_para"],
        schema=SCHEMA,
    )
    # Uma postagem EM VOO por (arquivo, conta): impede o mesmo vídeo sair duas
    # vezes na mesma conta. Também declarado no model (create_all dos testes).
    op.create_index(
        "uq_marketing_postagem_em_voo",
        "marketing_postagens",
        ["file_id", "rede_social_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('agendado', 'pendente', 'containering', 'publicando')"
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "redes_sociais_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "rede_social_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.redes_sociais.id", ondelete="CASCADE",
                name="fk_redes_sociais_tokens_rede_social_id_redes_sociais",
            ),
            nullable=False,
        ),
        sa.Column("external_user_id", sa.String(length=128), nullable=True),
        sa.Column("external_username", sa.Text(), nullable=True),
        # "facebook" (trilha com Página) ou "instagram" (Instagram Login):
        # é o token que decide o host da Graph, não a plataforma da linha.
        sa.Column(
            "provedor", sa.String(length=16), nullable=False,
            server_default=sa.text("'facebook'"),
        ),
        sa.Column("token_enc", sa.LargeBinary(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default=sa.text("'ok'")),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_ok_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "connected_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.users.id", ondelete="SET NULL",
                name="fk_redes_sociais_tokens_connected_by_users",
            ),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.UniqueConstraint("rede_social_id", name="uq_redes_sociais_tokens_rede_social_id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_redes_sociais_tokens_token_expires_at",
        "redes_sociais_tokens",
        ["token_expires_at"],
        schema=SCHEMA,
    )

    # Criativo → marca (de onde saem as contas de rede social).
    op.add_column(
        "marketing_creatives",
        sa.Column(
            "marca_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.marcas.id", ondelete="SET NULL",
                name="fk_marketing_creatives_marca_id_marcas",
            ),
            nullable=True,
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_marketing_creatives_marca_id", "marketing_creatives", ["marca_id"], schema=SCHEMA
    )
    # Backfill: o texto livre da coluna `marca` casa com o slug da marca
    # ('uranyx', 'charlots'…). O que não casar fica NULL e o operador escolhe
    # na tela (precedente de backfill em migration: 0273_logistica_amazon_canal).
    op.execute(
        sa.text(
            f"""
            UPDATE {SCHEMA}.marketing_creatives c
               SET marca_id = m.id
              FROM {SCHEMA}.marcas m
             WHERE c.marca_id IS NULL
               AND c.marca IS NOT NULL
               AND lower(btrim(c.marca)) = m.slug
            """  # noqa: S608 — SCHEMA é constante do módulo, não entrada
        )
    )

    # Postagem automática por CONTA: interruptor + tetos próprios (NULL = usa o
    # padrão do servidor). O Eduardo pediu limites configuráveis pra rodar
    # automático: quem decide quantos posts por dia é a conta, não o .env.
    op.add_column(
        "redes_sociais",
        sa.Column(
            "postagem_auto", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "redes_sociais", sa.Column("postagem_max_dia", sa.Integer(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "redes_sociais",
        sa.Column("postagem_intervalo_min", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )

    # Hash do arquivo (idempotência + trava de reuso entre marcas).
    op.add_column(
        "marketing_creative_files",
        sa.Column("sha256", sa.String(length=64), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_marketing_creative_files_sha256",
        "marketing_creative_files",
        ["sha256"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    for col in ("postagem_intervalo_min", "postagem_max_dia", "postagem_auto"):
        op.drop_column("redes_sociais", col, schema=SCHEMA)
    op.drop_index(
        "ix_marketing_creative_files_sha256",
        table_name="marketing_creative_files",
        schema=SCHEMA,
    )
    op.drop_column("marketing_creative_files", "sha256", schema=SCHEMA)
    op.drop_index(
        "ix_marketing_creatives_marca_id", table_name="marketing_creatives", schema=SCHEMA
    )
    op.drop_column("marketing_creatives", "marca_id", schema=SCHEMA)
    op.drop_index(
        "ix_redes_sociais_tokens_token_expires_at",
        table_name="redes_sociais_tokens",
        schema=SCHEMA,
    )
    op.drop_table("redes_sociais_tokens", schema=SCHEMA)
    for idx in (
        "uq_marketing_postagem_em_voo",
        "ix_marketing_postagens_agendado_para",
        "ix_marketing_postagens_rede_social_id",
        "ix_marketing_postagens_creative_id",
    ):
        op.drop_index(idx, table_name="marketing_postagens", schema=SCHEMA)
    op.drop_table("marketing_postagens", schema=SCHEMA)
