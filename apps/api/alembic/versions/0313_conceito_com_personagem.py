"""O conceito do vídeo autoral guarda qual personagem foi usado

Eduardo, 23/09/2026: "ao enviar uma ideia autoral ele não consegue escolher um
personagem, né? deveria dar pra escolher, ou não marcar que será outro".

Sem esta coluna a informação se perdia no caminho: a agência usa uma persona do
elenco, manda o vídeo, e quem decide não sabe qual rosto está na peça — nem o
briefing nasce ligado a ela quando o conceito é aprovado.

`ondelete=SET NULL` e não CASCADE: apagar o personagem não pode apagar o
registro de que a agência propôs e a casa decidiu. Nulo tem significado próprio
e legítimo — "sem persona", que é o caso das peças de mão e produto.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0313_conceito_com_personagem"
down_revision: str | None = "0312_conceito_espera_decisao"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "marketing_ideia_requisicoes",
        sa.Column("personagem_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_ideia_requisicoes_personagem",
        "marketing_ideia_requisicoes",
        "marketing_personagens",
        ["personagem_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_ideia_requisicoes_personagem",
        "marketing_ideia_requisicoes",
        schema=SCHEMA,
        type_="foreignkey",
    )
    op.drop_column("marketing_ideia_requisicoes", "personagem_id", schema=SCHEMA)
