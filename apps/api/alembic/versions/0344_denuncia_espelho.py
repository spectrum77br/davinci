"""denuncia_* — cópia do sistema de Fiscalização (Anúncios, Denúncias, Casos)

Vinicius, 30/09/2026: o sistema de Fiscalização da MAKISA sai do Hostinger e
roda no Mac mini da Makisa, junto do robô. O mini manda a cada 5 min o que
mudou em anúncios, lojas, denúncias, casos, compras, provas e verificações
(`POST /api/denuncia/sync/{tabela}`) e o DaVinci mostra as três telas da
seção Denúncia. Cada tabela guarda a linha original em `dados` (JSONB) e
repete em coluna só o que a tela filtra/ordena.

`denuncia_remetentes` nasce com o Mac mini da Makisa. Só o sha256 do token
vai aqui (o token fica em `~/.davinci_denuncia.json` no mini).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0344_denuncia_espelho"
down_revision: str | None = "0343_chamados_ia_abre_ml"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

MINI_MAKISA_TOKEN_SHA256 = "0ed8acaa0ffe70449e68f4cdf3f8b0957e4ae69ae81c2e108433bd8b5d314e5e"  # noqa: S105 — sha256, não o token


def _espelho() -> list[sa.Column]:
    return [
        sa.Column("dados", postgresql.JSONB(), nullable=False),
        sa.Column(
            "recebido_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "denuncia_anuncios",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("marketplace", sa.Text()),
        sa.Column("shop_id", sa.Text()),
        sa.Column("loja", sa.Text()),
        sa.Column("titulo", sa.Text()),
        sa.Column("grupo", sa.Text()),
        sa.Column("escopo", sa.Text()),
        sa.Column("situacao", sa.Text()),
        sa.Column("propria", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("vendas", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("visto_primeiro", sa.Text()),
        *_espelho(),
        schema=SCHEMA,
    )
    op.create_index("ix_denuncia_anuncios_shop_id", "denuncia_anuncios", ["shop_id"], schema=SCHEMA)

    op.create_table(
        "denuncia_lojas",
        sa.Column("shop_id", sa.Text(), primary_key=True),
        sa.Column("marketplace", sa.Text()),
        sa.Column("nome", sa.Text()),
        sa.Column("propria", sa.Integer(), nullable=False, server_default="0"),
        *_espelho(),
        schema=SCHEMA,
    )

    op.create_table(
        "denuncia_denuncias",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("anuncio_id", sa.Text()),
        sa.Column("canal", sa.Text()),
        sa.Column("protocolo", sa.Text()),
        sa.Column("data", sa.Text()),
        sa.Column("situacao", sa.Text()),
        sa.Column("resultado", sa.Text()),
        sa.Column("tipo", sa.Text()),
        sa.Column("prazo", sa.Text()),
        *_espelho(),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_denuncia_denuncias_anuncio_id", "denuncia_denuncias", ["anuncio_id"], schema=SCHEMA
    )
    op.create_index(
        "ix_denuncia_denuncias_canal_protocolo",
        "denuncia_denuncias",
        ["canal", "protocolo"],
        schema=SCHEMA,
    )

    op.create_table(
        "denuncia_casos",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("codigo", sa.Text()),
        sa.Column("anuncio_id", sa.Text()),
        sa.Column("status", sa.Text()),
        *_espelho(),
        schema=SCHEMA,
    )

    op.create_table(
        "denuncia_compras",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("anuncio_id", sa.Text()),
        sa.Column("caso_id", sa.BigInteger()),
        sa.Column("status", sa.Text()),
        *_espelho(),
        schema=SCHEMA,
    )

    op.create_table(
        "denuncia_provas",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("anuncio_id", sa.Text()),
        sa.Column("caso_id", sa.BigInteger()),
        sa.Column("denuncia_id", sa.BigInteger()),
        sa.Column("tipo", sa.Text()),
        sa.Column("sha256", sa.Text()),
        sa.Column("tamanho", sa.BigInteger()),
        sa.Column("arquivo_local", sa.Text()),
        *_espelho(),
        schema=SCHEMA,
    )
    op.create_index("ix_denuncia_provas_anuncio_id", "denuncia_provas", ["anuncio_id"], schema=SCHEMA)

    op.create_table(
        "denuncia_verificacoes",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("anuncio_id", sa.Text()),
        *_espelho(),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_denuncia_verificacoes_anuncio_id", "denuncia_verificacoes", ["anuncio_id"], schema=SCHEMA
    )

    op.create_table(
        "denuncia_remetentes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("nome", sa.Text(), nullable=False, unique=True),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("ultimo_envio_em", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        schema=SCHEMA,
    )
    op.execute(
        sa.text(
            f"INSERT INTO {SCHEMA}.denuncia_remetentes (id, nome, token_hash) "  # noqa: S608
            "VALUES (gen_random_uuid(), 'Mac mini da Makisa', :h)"
        ).bindparams(h=MINI_MAKISA_TOKEN_SHA256)
    )


def downgrade() -> None:
    for t in (
        "denuncia_remetentes",
        "denuncia_verificacoes",
        "denuncia_provas",
        "denuncia_compras",
        "denuncia_casos",
        "denuncia_denuncias",
        "denuncia_lojas",
        "denuncia_anuncios",
    ):
        op.drop_table(t, schema=SCHEMA)
