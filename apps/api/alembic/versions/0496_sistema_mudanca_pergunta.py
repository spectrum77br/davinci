"""Sistema › Mudanças: perguntas sobre como o sistema funciona (P-N).

Eduardo, 17/11/2026: o chefe pergunta pelo Claude "como funciona tal processo";
o robô do Mac lê o código do DaVinci (só lê) e a resposta, com trechos do
código, vai DIRETO para quem perguntou — sem aprovação. Mesma fila e mesmo
robô dos pedidos de mudança: coluna `tipo` ('mudanca' | 'pergunta'), coluna
`resposta` e o status novo 'respondida'.

Revision ID: 0496_sistema_mudanca_pergunta
Revises: 0495_conciliacao_temu
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0496_sistema_mudanca_pergunta"
down_revision: str | None = "0495_conciliacao_temu"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
STATUS_ANTES = (
    "pedida", "fazendo", "pronta", "aprovada", "publicando", "publicada", "recusada",
    "precisa_equipe", "falhou",
)
STATUS = (*STATUS_ANTES, "respondida")


def _in(coluna: str, valores: tuple[str, ...]) -> str:
    return f"{coluna} IN ({', '.join(repr(v) for v in valores)})"


def upgrade() -> None:
    op.add_column(
        "sistema_mudanca",
        sa.Column("tipo", sa.String(20), server_default=sa.text("'mudanca'"), nullable=False),
        schema=SCHEMA,
    )
    op.add_column("sistema_mudanca", sa.Column("resposta", sa.Text(), nullable=True), schema=SCHEMA)
    op.create_check_constraint(
        op.f("ck_sistema_mudanca_tipo"),
        "sistema_mudanca",
        _in("tipo", ("mudanca", "pergunta")),
        schema=SCHEMA,
    )
    op.drop_constraint(
        op.f("ck_sistema_mudanca_status"), "sistema_mudanca", type_="check", schema=SCHEMA
    )
    op.create_check_constraint(
        op.f("ck_sistema_mudanca_status"), "sistema_mudanca", _in("status", STATUS), schema=SCHEMA
    )


def downgrade() -> None:
    op.execute("DELETE FROM davinci.sistema_mudanca WHERE tipo = 'pergunta'")
    op.drop_constraint(
        op.f("ck_sistema_mudanca_status"), "sistema_mudanca", type_="check", schema=SCHEMA
    )
    op.create_check_constraint(
        op.f("ck_sistema_mudanca_status"),
        "sistema_mudanca",
        _in("status", STATUS_ANTES),
        schema=SCHEMA,
    )
    op.drop_constraint(
        op.f("ck_sistema_mudanca_tipo"), "sistema_mudanca", type_="check", schema=SCHEMA
    )
    op.drop_column("sistema_mudanca", "resposta", schema=SCHEMA)
    op.drop_column("sistema_mudanca", "tipo", schema=SCHEMA)
