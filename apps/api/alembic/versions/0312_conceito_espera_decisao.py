"""O conceito do vídeo autoral volta a esperar decisão, agora com o vídeo junto

Eduardo, 23/09/2026: "ele seguiu vindo pra roteiro sem aprovação nenhuma; antes
não foi pra pedidos".

## Por que a tabela volta depois de eu tê-la apagado hoje de manhã

Na 0310 eu criei uma fila de pedidos. Na 0311 apaguei, argumentando que o
conceito podia viver como `marketing_roteiros` e que uma segunda fila seria um
segundo lugar para alguém esquecer de olhar.

O argumento errava o alvo. Mesmo com `ativo=false`, o conceito entrava na LISTA
de roteiros da casa sem ninguém ter decidido nada, misturado aos briefings que
a equipe escreveu. Essa lista é o que a casa escreveu; texto que chega de fora
não entra nela por chegada.

## A diferença para a 0310

`creative_id`. A 0310 era pedido de ideia SEM vídeo — autorizar antes de
produzir. Esta é o conceito de um vídeo que JÁ CHEGOU: quem decide assiste à
peça na mesma tela, em vez de julgar uma descrição. As duas perguntas seguem
separadas — "este vídeo presta?" é o `aprovado` do criativo, "esta ideia vira
briefing nosso?" é esta fila — e podem ser respondidas de formas diferentes.

Sai `justificativa` (campo que ninguém chegou a preencher) e entra `creative_id`.
"""

# ruff: noqa: S608 — `SCHEMA` é constante do módulo, não entrada de usuário.
# Mesma dispensa das migrations 0037 e 0039.

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0312_conceito_espera_decisao"
down_revision: str | None = "0311_ideia_vira_video_autoral"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "marketing_ideia_requisicoes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("titulo", sa.String(160), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=False),
        sa.Column("marca", sa.String(120), nullable=True),
        sa.Column("sku", sa.String(120), nullable=True),
        sa.Column("equipe", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'pendente'")),
        sa.Column("motivo", sa.Text(), nullable=True),
        # O vídeo que trouxe o conceito. SET NULL nos dois: apagar a peça ou o
        # briefing não pode apagar o registro de que alguém propôs e decidiu.
        sa.Column("creative_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("roteiro_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decidido_por", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decidido_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["creative_id"], [f"{SCHEMA}.marketing_creatives.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["roteiro_id"], [f"{SCHEMA}.marketing_roteiros.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["decidido_por"], [f"{SCHEMA}.users.id"], ondelete="SET NULL"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_marketing_ideia_requisicoes_fila",
        "marketing_ideia_requisicoes",
        ["status", "created_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_marketing_ideia_requisicoes_equipe",
        "marketing_ideia_requisicoes",
        ["equipe"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_marketing_ideia_requisicoes_creative_id",
        "marketing_ideia_requisicoes",
        ["creative_id"],
        schema=SCHEMA,
    )

    # Os conceitos que a 0311 deixou virar roteiro desligado voltam para a fila.
    # São poucos (o caminho autoral abriu hoje) e todos endereçados a uma
    # agência, sem origem e sem autor interno — é essa combinação que os separa
    # de um briefing escrito pela casa.
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.marketing_ideia_requisicoes
            (id, titulo, descricao, marca, sku, equipe, status, creative_id, created_at, updated_at)
        SELECT gen_random_uuid(), r.titulo, r.texto, r.marca, r.sku,
               c.equipe, 'pendente', c.id, r.created_at, now()
          FROM {SCHEMA}.marketing_creatives c
          JOIN {SCHEMA}.marketing_roteiros r ON r.id = c.roteiro_id
         WHERE c.aprovado IS NULL
           AND r.ativo = false
           AND r.origem_id IS NULL
           AND r.created_by IS NULL
           AND r.equipe_destino IS NOT NULL
        """
    )
    # E o roteiro que não devia ter nascido sai de cena: a linha da entrega
    # solta o ponteiro e o briefing desaparece da lista da casa.
    op.execute(
        f"""
        DELETE FROM {SCHEMA}.marketing_roteiros r
         USING {SCHEMA}.marketing_ideia_requisicoes q
        WHERE q.descricao = r.texto AND q.titulo = r.titulo
          AND r.ativo = false AND r.origem_id IS NULL
          AND r.created_by IS NULL AND r.equipe_destino IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_marketing_ideia_requisicoes_creative_id",
        table_name="marketing_ideia_requisicoes",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_marketing_ideia_requisicoes_equipe",
        table_name="marketing_ideia_requisicoes",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_marketing_ideia_requisicoes_fila",
        table_name="marketing_ideia_requisicoes",
        schema=SCHEMA,
    )
    op.drop_table("marketing_ideia_requisicoes", schema=SCHEMA)
