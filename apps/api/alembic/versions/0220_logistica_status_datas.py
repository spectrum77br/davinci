"""logistica.status_datas: quando cada campo do Status Plataforma mudou.

A coluna "Status Plataforma" da Logística mostra a assinatura do canal
(cancelado / não entregue / retido na alfândega / ...), mas não dizia DESDE
QUANDO cada um daqueles pedaços está assim. Esta coluna guarda um carimbo por
campo:

    {"ship_substatus": {"em": "2026-08-12T08:05:00+00:00", "fonte": "aprox"}}

`fonte` = plataforma (data oficial do canal) | aprox (o canal datou o recurso,
não o campo) | davinci (carimbamos quando vimos mudar). Ver
app.services.logistica_datas.

Aditiva e vazia por padrão: linha antiga fica com {} e vai se carimbando
sozinha no próximo 🔄/recarregar. Nada é reescrito.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0220_logistica_status_datas"
down_revision: str | None = "0219_remove_logistica_status_desconsiderar"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "logistica",
        sa.Column(
            "status_datas",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("logistica", "status_datas", schema=SCHEMA)
