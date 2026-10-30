"""atendimento_lojas_pessoa — as lojas de cada pessoa no /atendimento

Eduardo, 30/10/2026: "será separado as lojas no atendimento por pessoas então
x usuario vai poder ver x lojas e outros não isso também se aplica pra caixa de
e-mail das lojas delas" (e a Garantia: "eles só vão poder ver das lojas
deles"). A tabela guarda, por pessoa, as chaves das lojas que ela vê:
"f:<ficha>", "i:<integração>" e "c:<canal do robô>"
(`app/models/atendimento_lojas_pessoa.py`).

Nasce VAZIA: quem não é admin e não tem linha deixa de ver as lojas no
atendimento. A divisão da planilha "Separação de contas" (londres, israel,
marrocos) entra pelo SQL do deploy (output/atendimento_lojas_pessoa_3010.sql),
com os ids de produção; a tela de Usuários edita depois.

Revision ID: 0440_atendimento_lojas_pessoa
Revises: 0439_garantia_pedido
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0440_atendimento_lojas_pessoa"
down_revision: str | None = "0439_garantia_pedido"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
SCHEMA = "davinci"

# FIXO (não importa a constante viva do modelo).
PADRAO_CHAVE = r"^[ifc]:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


def upgrade() -> None:
    op.create_table(
        "atendimento_lojas_pessoa",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("chave", sa.String(40), primary_key=True),
        sa.Column(
            "criado_por",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        # Nome final (`op.f`), o mesmo que a convenção ck_<tabela>_<nome> dá ao modelo.
        sa.CheckConstraint(
            f"chave ~ '{PADRAO_CHAVE}'", name=op.f("ck_atendimento_lojas_pessoa_chave")
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("atendimento_lojas_pessoa", schema=SCHEMA)
