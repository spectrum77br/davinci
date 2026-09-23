"""Ideia proposta pela agência: ela propõe, a casa libera

Eduardo, 23/09/2026: "deixa eles publicarem também em alguma parte um vídeo 100%
feito por eles, descrição tudo (...) daí vira pedidos no DaVinci que deve a nossa
aprovação pra eles poderem gerar o que quiserem".

## Por que tabela própria, e não `marketing_roteiros.ativo = false`

Os dois estados parecem iguais e significam o oposto. `ativo = false` é "briefing
DA CASA ainda não liberado" — o interruptor de revisão do que a casa escreveu.
Uma proposta da agência é "texto DELA que ainda não foi aceito". Num campo só, a
lista interna misturaria ideia que a casa não soltou com ideia que a casa não
aceitou, e ninguém saberia qual botão resolve qual.

A aprovação CRIA o roteiro, endereçado a quem pediu, e `roteiro_id` guarda o
rastro. A recusa guarda o motivo — é o que impede o mesmo pedido de voltar igual
na semana seguinte, a mesma lição da requisição de personagem.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0310_ideia_proposta_pela_agencia"
down_revision: str | None = "0309_rede_social_adspower"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "marketing_ideia_requisicoes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("titulo", sa.String(160), nullable=False),
        # A ideia inteira. Vira o `texto` do roteiro quando alguém aprova.
        sa.Column("descricao", sa.Text(), nullable=False),
        # Argumento para quem decide, não instrução para quem produz: fica no
        # pedido e NÃO desce para o roteiro.
        sa.Column("justificativa", sa.Text(), nullable=True),
        sa.Column("marca", sa.String(120), nullable=True),
        sa.Column("sku", sa.String(120), nullable=True),
        sa.Column("equipe", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'pendente'")),
        sa.Column("motivo", sa.Text(), nullable=True),
        # SET NULL, não CASCADE: apagar o briefing não pode apagar o registro de
        # que a agência pediu e a casa deixou. O pedido é o rastro da decisão.
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
    # A fila de quem decide (status + chegada) e o recorte de quem pediu.
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


def downgrade() -> None:
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
