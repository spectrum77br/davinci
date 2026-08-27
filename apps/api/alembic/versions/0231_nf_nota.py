"""Tabela das NF-e emitidas — o XML autorizado pela SEFAZ vira registro no davinci.

O robô da nuvem já baixa o XML de cada nota que sai (Upseller/Bling) pro
Downloads do servidor Windows. O davinci não tinha ONDE guardar a nota:
`nf_faturamento` só tem status e `nf_etiqueta_arquivo` só tem PDF. Esta tabela
guarda o que a nota tem de fato (número, chave, valor, emitente, protocolo) e
o XML assinado inteiro — o arquivo é a prova fiscal.

Chave natural = `chave` (44 dígitos), então re-subir o mesmo XML atualiza a
mesma linha em vez de duplicar.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0231_nf_nota"
down_revision: str | None = "0230_vigia_importacao"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "nf_nota",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("chave", sa.Text(), nullable=False),
        sa.Column("pedido_bling", sa.Text(), nullable=True),
        sa.Column("numero", sa.Text(), nullable=False),
        sa.Column("serie", sa.Text(), nullable=True),
        sa.Column("emitente_cnpj", sa.Text(), nullable=True),
        sa.Column("emitente_nome", sa.Text(), nullable=True),
        sa.Column("destinatario_doc", sa.Text(), nullable=True),
        sa.Column("destinatario_nome", sa.Text(), nullable=True),
        sa.Column("valor", sa.Numeric(14, 2), nullable=True),
        sa.Column("data_emissao", sa.DateTime(timezone=True), nullable=True),
        sa.Column("protocolo", sa.Text(), nullable=True),
        sa.Column("situacao", sa.Text(), nullable=True),
        sa.Column("situacao_motivo", sa.Text(), nullable=True),
        sa.Column("upseller_pedido", sa.Text(), nullable=True),
        sa.Column("xml", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("chave", name="uq_nf_nota_chave"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_nf_nota_pedido_bling", "nf_nota", ["pedido_bling"], schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_index("ix_nf_nota_pedido_bling", table_name="nf_nota", schema=SCHEMA)
    op.drop_table("nf_nota", schema=SCHEMA)
