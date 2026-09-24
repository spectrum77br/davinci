"""chamados_ia_avaliacoes — ✓ acertou / ✗ errou nas decisões da IA de Chamado

Vinicius, 24/09/2026: "como eu faço pra dizer: nesse você errou, nesse você
acertou". Uma avaliação por análise da IA. O ✗ traz a correção, que vira
instrução no chamado (a IA refaz) e aprendizado (a IA recebe correções e
confirmações a cada passada, junto com o manual).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0321_ia_avaliacoes"
down_revision: str | None = "0320_ia_de_chamado"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "chamados_ia_avaliacoes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "mensagem_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.chamado_mensagem.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "chamado_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.chamados.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("certo", sa.Boolean(), nullable=False),
        sa.Column("correcao", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_chamados_ia_avaliacoes_chamado_id",
        "chamados_ia_avaliacoes",
        ["chamado_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("chamados_ia_avaliacoes", schema=SCHEMA)
