"""A proposta da agência deixa de ser pedido e passa a ser vídeo pronto

Eduardo, 23/09/2026, corrigindo o de mais cedo no mesmo dia: "o form ali da
ideia deles era pra ser mais um publicar vídeo para análise, que aí eles
preenchiam e já poderiam enviar o vídeo, e esse iria para revisão direto".

## Por que a tabela sai inteira, e não fica desligada

`marketing_ideia_requisicoes` (migration 0310, algumas horas de vida, zero
linhas) servia a um fluxo de DUAS etapas: autorizar a ideia, depois produzir.
O fluxo pedido tem UMA: a agência manda o vídeo com o conceito junto, e ele cai
na revisão que já existe — `marketing_creatives.aprovado = NULL`, com aprovar,
recusar e feedback prontos desde a 0299.

Deixar a tabela parada criaria duas filas para a mesma pergunta ("isto presta?")
e um formulário órfão que ninguém alimenta. Feature pela metade não envelhece
bem: daqui a um mês ninguém lembra por que a fila de Pedidos tem uma seção que
nunca enche.

O conceito não se perde: ele vira um `marketing_roteiros` ligado ao criativo por
`roteiro_id` — que é o que essa coluna já significa ("o briefing que esta linha
cumpre"). De quebra, o elo roteiro→criativo, que estava em 5 de 49 em 22/09,
passa a nascer preenchido também no caminho autoral.

O downgrade recria a tabela vazia, que é o estado exato em que ela é apagada.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0311_ideia_vira_video_autoral"
down_revision: str | None = "0310_ideia_proposta_pela_agencia"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
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


def downgrade() -> None:
    op.create_table(
        "marketing_ideia_requisicoes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("titulo", sa.String(160), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=False),
        sa.Column("justificativa", sa.Text(), nullable=True),
        sa.Column("marca", sa.String(120), nullable=True),
        sa.Column("sku", sa.String(120), nullable=True),
        sa.Column("equipe", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'pendente'")),
        sa.Column("motivo", sa.Text(), nullable=True),
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
