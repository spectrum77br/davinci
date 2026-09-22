"""Versão do roteiro pela agência, e requisição de personagem com procedência

Eduardo, 22/09/2026: "queria que deixasse criar personagem no studio dai iria
como requisicao pra aprovar se aprovado cria o personagem, e roteiros ele pode
editar se quiser pois roteiro agora se tornara roteiros/ideias entao o canal de
criativos pode alterar se quiser".

## Por que VERSÃO e não edição por cima

O roteiro passou a ser ideia, e ideia boa se discute. Mas deixar a agência
reescrever o campo apaga o "antes": some a forma de saber se a versão que a casa
propôs prestava, e é exatamente essa comparação que ensina o sistema — o mesmo
raciocínio de `marketing_creatives.aprovado`. Somado a isso, `marketing_roteiros`
não tem versionamento: dois lados editando o mesmo `texto` é último a salvar
ganha, em silêncio.

`origem_id` aponta para o roteiro de onde a versão saiu. O original continua
intacto e os dois ficam lado a lado.

## Por que a requisição de personagem é tabela própria

Ela carrega o que um personagem não carrega: de onde veio o rosto, de onde veio
a voz, e se existe cessão escrita. Isso não é burocracia — é a regra que a
Súmula 403 do STJ impõe (uso comercial de imagem basta, não precisa provar
dano), e sem os campos de procedência a tela vira um cano que importa passivo
para dentro de casa com um clique em "aprovar".

Os campos de origem são NOT NULL de propósito. Requisição sem dizer de onde veio
o rosto não deveria nem chegar à mesa de quem aprova.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0306_roteiro_versao_e_requisicao_de_personagem"
down_revision: str | None = "0305_imagem_publica"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "marketing_roteiros",
        sa.Column("origem_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_marketing_roteiros_origem",
        "marketing_roteiros",
        "marketing_roteiros",
        ["origem_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        # A versão sobrevive ao original: apagar a ideia de partida não pode
        # levar junto o trabalho que a agência fez em cima dela.
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_marketing_roteiros_origem_id",
        "marketing_roteiros",
        ["origem_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "marketing_personagem_requisicoes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("nome", sa.String(120), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=True),
        sa.Column("justificativa", sa.Text(), nullable=True),
        # ── procedência: a parte que existe por causa da Súmula 403 ──
        sa.Column("origem_imagem", sa.Text(), nullable=False),
        sa.Column("origem_voz", sa.Text(), nullable=False),
        sa.Column("cessao", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("cessao_obs", sa.Text(), nullable=True),
        # Quem pediu. Mesma string de equipe que o token do portal carrega.
        sa.Column("equipe", sa.String(64), nullable=False),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default=sa.text("'pendente'")
        ),
        sa.Column("motivo", sa.Text(), nullable=True),
        # Preenchido na aprovação: é o rastro de que esta requisição virou aquele
        # personagem, e o que permite auditar a procedência depois.
        sa.Column("personagem_id", postgresql.UUID(as_uuid=True), nullable=True),
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
            ["personagem_id"],
            [f"{SCHEMA}.marketing_personagens.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["decidido_por"], [f"{SCHEMA}.users.id"], ondelete="SET NULL"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_marketing_personagem_requisicoes_fila",
        "marketing_personagem_requisicoes",
        ["status", "created_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_marketing_personagem_requisicoes_fila",
        table_name="marketing_personagem_requisicoes",
        schema=SCHEMA,
    )
    op.drop_table("marketing_personagem_requisicoes", schema=SCHEMA)
    op.drop_index(
        "ix_marketing_roteiros_origem_id", table_name="marketing_roteiros", schema=SCHEMA
    )
    op.drop_constraint(
        "fk_marketing_roteiros_origem", "marketing_roteiros", schema=SCHEMA, type_="foreignkey"
    )
    op.drop_column("marketing_roteiros", "origem_id", schema=SCHEMA)
