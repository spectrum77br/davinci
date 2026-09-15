"""Assinatura por marca e canal, independente dos modelos de mensagem."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0278_email_assinaturas"
down_revision = "0277_marcas_redes_sociais"
branch_labels = None
depends_on = None
SCHEMA = "davinci"


def upgrade():
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
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("marca_id", "contexto", name="uq_marca_email_assinatura_canal"),
        schema=SCHEMA,
    )
    # Preserva as combinações já cadastradas. O corpo da mensagem anterior
    # não é uma assinatura e não deve ser copiado para o rodapé.
    op.execute(
        sa.text("""
        INSERT INTO davinci.marca_email_assinaturas
            (id, marca_id, contexto, texto, incluir_logo, incluir_dados_marca, ativo)
        SELECT gen_random_uuid(), marca_id, contexto, '',
               bool_or(incluir_logo), bool_or(incluir_assinatura), bool_or(ativo)
        FROM davinci.marca_email_padroes
        GROUP BY marca_id, contexto
    """)
    )


def downgrade():
    op.drop_table("marca_email_assinaturas", schema=SCHEMA)
