"""denuncia_anexos — provas anexadas pelo DaVinci (ficha do caso) para o robô do mini

Vinicius, 01/10/2026: "chegou o produto, onde eu vou colocar as provas?" — antes só dava
no sistema dentro do Mac mini. Agora a ficha do caso tem "Anexar prova" (vídeo como link
do MEGA, foto, NF-e, fatura, tela do pedido, devolução): o arquivo fica aqui, o mini busca
(/api/denuncia/sync/anexos), entrega ao sistema de lá (que guarda e sobe pro MEGA) e
responde; a prova volta na cópia de 5 em 5 min.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0354_denuncia_anexos"
down_revision: str | None = "0353_atendimento_etiquetas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "denuncia_anexos",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("caso_id", sa.BigInteger()),
        sa.Column("anuncio_id", sa.Text(), nullable=False),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("nome", sa.Text()),
        sa.Column("tamanho", sa.BigInteger()),
        sa.Column("sha256", sa.Text()),
        sa.Column("arquivo_local", sa.Text()),
        sa.Column("link", sa.Text()),
        sa.Column("obs", sa.Text()),
        sa.Column("enviado_por", sa.Text()),
        sa.Column(
            "enviado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("entregue_em", sa.DateTime(timezone=True)),
        sa.Column("ok", sa.Boolean()),
        sa.Column("resultado", sa.Text()),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_denuncia_anexos_caso", "denuncia_anexos", ["caso_id"], schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_index("ix_denuncia_anexos_caso", table_name="denuncia_anexos", schema=SCHEMA)
    op.drop_table("denuncia_anexos", schema=SCHEMA)
