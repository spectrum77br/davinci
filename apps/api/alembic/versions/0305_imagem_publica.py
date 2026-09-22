"""Imagem pública no banco (link sem login) + o logo dos Correios

Vinicius, 22/09/2026: "sobe essa imagem no servidor e me passa o link dela… no
banco de dados". O servidor não tem pasta de upload pra app — e desde a
blindagem de hoje ninguém escreve arquivo nele fora do deploy —, então a imagem
vive no Postgres e sai por `GET /api/imagens/{id}`, que é ABERTO (o painel
inteiro exige login, e um link que só abre logado não serve pra colar em aviso,
mensagem ou cartão do robô).

O PNG do logo dos Correios (205x161, 12.736 bytes) entra aqui como dado da
migração, com id fixo, porque é o único caminho que existe hoje pra pôr um
arquivo no banco de produção.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0305_imagem_publica"
down_revision: str | None = "0304_link_envio_texto_legado"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
CORREIOS_ID = "b6a1f2c4-5d3e-4a7b-9c81-0f2e3d4c5b6a"
CORREIOS_B64 = (
    "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"
)


def upgrade() -> None:
    op.create_table(
        "imagem_publica",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False, server_default="image/png"),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("blob", postgresql.BYTEA(), nullable=False),
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
        schema=SCHEMA,
    )
    op.execute(
        sa.text(
            f"INSERT INTO {SCHEMA}.imagem_publica (id, nome, content_type, size_bytes, blob) "
            "VALUES (:id, :nome, :ct, :tam, decode(:b64, 'base64'))"
        ).bindparams(
            id=CORREIOS_ID,
            nome="correios.png",
            ct="image/png",
            tam=12736,
            b64=CORREIOS_B64,
        )
    )


def downgrade() -> None:
    op.drop_table("imagem_publica", schema=SCHEMA)
