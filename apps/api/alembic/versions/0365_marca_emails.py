"""marca_emails — os e-mails do Tuta de cada marca no lugar das assinaturas

Eduardo, 05/10/2026: na aba Cadastros › E-mails "deixar registrar só os e-mails das que temos em
redes sociais… o e-mail de sac, o de atacado e o de dúvidas, cada um no Tuta" e tirar a matriz de
assinaturas — os endereços são aliases da conta principal do Tuta, que tem uma assinatura só.
`marca_email_assinaturas` estava vazia em produção (0 linhas em 05/10); o downgrade a recria vazia.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0365_marca_emails"
down_revision: str | None = "0364_denuncia_relatorios"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
SCHEMA = "davinci"


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "marca_emails",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "marca_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.marcas.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tipo", sa.String(16), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("marca_id", "tipo", name="uq_marca_emails_tipo"),
        schema=SCHEMA,
    )
    op.drop_table("marca_email_assinaturas", schema=SCHEMA)


def downgrade() -> None:
    op.create_table(
        "marca_email_assinaturas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "marca_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.marcas.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("contexto", sa.String(32), nullable=False),
        sa.Column("texto", sa.Text(), nullable=False, server_default=""),
        sa.Column("incluir_logo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("incluir_dados_marca", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        sa.UniqueConstraint("marca_id", "contexto", name="uq_marca_email_assinatura_canal"),
        schema=SCHEMA,
    )
    op.drop_table("marca_emails", schema=SCHEMA)
