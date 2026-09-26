"""companies: porta, usuário e senha do proxy de cada empresa

Eduardo (26/09/2026): "o que você acha de colocarmos usuário e senha, um toogle
bem organizado, aí quando mudarmos o ip corrige corretamente e salva e já deixa
no ar". Os proxies novos (VPS próprias, 26/09) têm usuário e senha POR IP, que
o serviço do Mac não conhecia: sem isso a troca de IP na tela dava ✗.

- proxy_tipo / proxy_porta / proxy_usuario: o resto do endereço do proxy.
- proxy_senha_enc: a senha cifrada (AES-GCM, mesma chave dos certificados).
- adspower_perfis_extras: números de perfil do AdsPower da empresa que nenhuma
  loja aponta (ex.: os perfis de Contabilidade/Financeiro 2) — o serviço troca
  esses também.
- proxy_rev: sobe a cada mudança de IP/proxy; o serviço devolve o número que
  aplicou e o resultado de uma versão velha não marca a nova como aplicada.

Revision ID: 0332_companies_proxy
Revises: 0331_historico
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0332_companies_proxy"
down_revision: str | None = "0331_historico"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    # Tabela pequena (~50 empresas) e colunas novas vazias/defaults constantes:
    # instantâneo. O limite só evita travar o deploy atrás de outra transação.
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.add_column("companies", sa.Column("proxy_tipo", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column("companies", sa.Column("proxy_porta", sa.Integer(), nullable=True), schema=SCHEMA)
    op.add_column("companies", sa.Column("proxy_usuario", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column("companies", sa.Column("proxy_senha_enc", sa.LargeBinary(), nullable=True), schema=SCHEMA)
    op.add_column(
        "companies",
        sa.Column(
            "adspower_perfis_extras",
            pg.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "companies",
        sa.Column("proxy_rev", sa.Integer(), nullable=False, server_default=sa.text("0")),
        schema=SCHEMA,
    )


def downgrade() -> None:
    for col in ("proxy_rev", "adspower_perfis_extras", "proxy_senha_enc", "proxy_usuario", "proxy_porta", "proxy_tipo"):
        op.drop_column("companies", col, schema=SCHEMA)
