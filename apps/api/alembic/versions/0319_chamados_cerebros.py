"""chamados_cerebros — o cérebro dos chamados com senha própria (Hermes)

Vinicius, 24/09/2026: "começar a cadastrar via Hermes esse cérebro" no Mac
Santiago, substituindo o cérebro que roda no computador do Eduardo.

O cérebro antigo usa o mesmo token do robô de NF (`NF_AGENT_TOKEN`) e assina só
"cérebro". Com isso não dava pra saber quem decidiu, nem desligar um cérebro
sem derrubar as mãos do Eduardo (lease, resultado, recebida, que usam o mesmo
token). E o `/agent/analisar` não reserva o chamado: dois cérebros juntos
respondem o mesmo caso duas vezes pra plataforma.

A linha do Hermes nasce com `exclusivo = false` — nada muda no deploy. A troca
de guarda é o próprio Hermes chamando `POST /api/chamados/agent/cerebro`
`{"exclusivo": true}` quando estiver pronto; desfaz com `false`.

Só o sha256 do token vai aqui (o token fica no Mac Santiago).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0319_chamados_cerebros"
down_revision: str | None = "0318_criativo_produto_irmao"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

HERMES_TOKEN_SHA256 = "6e32134edfee532338671224d3f7e7264a0071b0e1e3205f86387abea052b4e3"  # noqa: S105 — sha256, não o token


def upgrade() -> None:
    op.create_table(
        "chamados_cerebros",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("nome", sa.Text(), nullable=False, unique=True),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("exclusivo", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("legado_ignorado_at", sa.DateTime(timezone=True), nullable=True),
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
            f"INSERT INTO {SCHEMA}.chamados_cerebros (id, nome, token_hash) "  # noqa: S608
            "VALUES (gen_random_uuid(), 'Hermes', :h)"
        ).bindparams(h=HERMES_TOKEN_SHA256)
    )


def downgrade() -> None:
    op.drop_table("chamados_cerebros", schema=SCHEMA)
