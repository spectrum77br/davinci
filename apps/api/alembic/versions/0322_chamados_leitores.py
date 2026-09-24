"""chamados_leitores — o executor de leitura de chamado com senha própria

Vinicius, 24/09/2026 (296012 / 2609200FUTKM4JD): a recusa escrita da Shopee
("Histórico da Solicitação") só existe no Seller Center. A API da Shopee diz só
"aguardando análise" — às 16:09 ainda dizia isso de uma recusa das 15:59 — e o
chamado ficou mudo. Quem passa a ler é um executor novo no Mac Santiago
(pasta `executor-leitura-chamado`), pelo AdsPower.

Senha própria, separada do `NF_AGENT_TOKEN` (as mãos do Eduardo, que postam na
conversa com o cliente): este robô só pede a lista e devolve o que leu.

Só o sha256 do token vai aqui (o token fica no .env do Mac Santiago).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0322_chamados_leitores"
down_revision: str | None = "0321_ia_avaliacoes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

LEITOR_TOKEN_SHA256 = "3986c686b727ecb2a760a590b9ec338e5859c0c07f7d75ea6045f08612189093"  # noqa: S105 — sha256, não o token


def upgrade() -> None:
    op.create_table(
        "chamados_leitores",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("nome", sa.Text(), nullable=False, unique=True),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        schema=SCHEMA,
    )
    op.execute(
        sa.text(
            f"INSERT INTO {SCHEMA}.chamados_leitores (id, nome, token_hash) "  # noqa: S608
            "VALUES (gen_random_uuid(), 'Executor de leitura (Santiago)', :h)"
        ).bindparams(h=LEITOR_TOKEN_SHA256)
    )


def downgrade() -> None:
    op.drop_table("chamados_leitores", schema=SCHEMA)
