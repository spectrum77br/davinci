"""IA de Chamado — liga/desliga e o manual (regras)

Vinicius, 24/09/2026: o cérebro dos chamados ganha nome e lugar na aba — "IA de
Chamado" —, onde ele ensina ("quando acontecer isso, faça isso") e liga/desliga.

- `chamados_cerebros.ligada`: desligada = não recebe nada e não decide; ligada =
  decide de verdade (sem modo teste — decisão dele). Nasce desligada: ligar é
  ato da pessoa, pela aba.
- `chamados_ia_regras`: o manual. A IA lê as regras ativas a cada passada.
- A linha do Hermes vira "IA de Chamado" (é o nome que vai no histórico).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0320_ia_de_chamado"
down_revision: str | None = "0319_chamados_cerebros"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "chamados_cerebros",
        sa.Column("ligada", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        schema=SCHEMA,
    )
    op.execute(
        sa.text(
            f"UPDATE {SCHEMA}.chamados_cerebros SET nome = 'IA de Chamado' "  # noqa: S608
            "WHERE nome = 'Hermes'"
        )
    )
    op.create_table(
        "chamados_ia_regras",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("quando", sa.Text(), nullable=False),
        sa.Column("faca", sa.Text(), nullable=False),
        sa.Column("plataforma", sa.Text(), nullable=True),
        sa.Column("ativa", sa.Boolean(), nullable=False, server_default=sa.text("true")),
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


def downgrade() -> None:
    op.drop_table("chamados_ia_regras", schema=SCHEMA)
    op.execute(
        sa.text(
            f"UPDATE {SCHEMA}.chamados_cerebros SET nome = 'Hermes' "  # noqa: S608
            "WHERE nome = 'IA de Chamado'"
        )
    )
    op.drop_column("chamados_cerebros", "ligada", schema=SCHEMA)
