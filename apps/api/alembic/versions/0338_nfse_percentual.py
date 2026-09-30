"""NFS-e: nota fixa de percentual sobre uma base digitada na hora

Eduardo (29/09/2026): "quero emitir uma nota de serviço de 0,5%". O % incide
sobre um valor que ele digita na hora de emitir (a base): base R$ 200.000,00 ×
0,5% = nota de R$ 1.000,00. Por isso:

- `nfse_modelo.tipo_valor`: 'fixo' (como antes, `valor` obrigatório) ou
  'percentual' (`percentual` obrigatório, `base_padrao` só sugere a base).
- `nfse_emissao.base_calculo` / `percentual`: a conta que gerou o valor da
  nota, pra reenviar a recusada com os mesmos números e mostrar no histórico.

Espelha `app/models/nfse.py`.

Revision ID: 0338_nfse_percentual
Revises: 0337_nfse_servico
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0338_nfse_percentual"
down_revision: str | None = "0337_nfse_servico"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")

    op.add_column(
        "nfse_modelo",
        sa.Column("tipo_valor", sa.Text(), server_default="fixo", nullable=False),
        schema=SCHEMA,
    )
    op.add_column(
        "nfse_modelo", sa.Column("percentual", sa.Numeric(9, 4), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "nfse_modelo", sa.Column("base_padrao", sa.Numeric(15, 2), nullable=True), schema=SCHEMA
    )
    op.alter_column(
        "nfse_modelo", "valor", existing_type=sa.Numeric(15, 2), nullable=True, schema=SCHEMA
    )
    # op.f(): o nome já vem pronto. Sem ele a naming convention do metadata
    # ("ck_%(table_name)s_%(constraint_name)s") prefixa de novo (ver 0256) e o
    # nome no banco não bate com o do model.
    op.create_check_constraint(
        op.f("ck_nfse_modelo_tipo_valor"),
        "nfse_modelo",
        "tipo_valor IN ('fixo', 'percentual')",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_nfse_modelo_valor_tipo"),
        "nfse_modelo",
        "(tipo_valor = 'fixo' AND valor IS NOT NULL)"
        " OR (tipo_valor = 'percentual' AND percentual IS NOT NULL)",
        schema=SCHEMA,
    )

    op.add_column(
        "nfse_emissao", sa.Column("base_calculo", sa.Numeric(15, 2), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "nfse_emissao", sa.Column("percentual", sa.Numeric(9, 4), nullable=True), schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_column("nfse_emissao", "percentual", schema=SCHEMA)
    op.drop_column("nfse_emissao", "base_calculo", schema=SCHEMA)

    for nome in ("ck_nfse_modelo_valor_tipo", "ck_nfse_modelo_tipo_valor"):
        op.drop_constraint(op.f(nome), "nfse_modelo", type_="check", schema=SCHEMA)
    # Nota de percentual não tem valor: volta com a conta sobre a base sugerida
    # (0 se não tinha base) pra coluna poder voltar a ser NOT NULL.
    op.execute(
        "UPDATE davinci.nfse_modelo"
        " SET valor = ROUND(COALESCE(base_padrao, 0) * percentual / 100, 2)"
        " WHERE valor IS NULL"
    )
    op.alter_column(
        "nfse_modelo", "valor", existing_type=sa.Numeric(15, 2), nullable=False, schema=SCHEMA
    )
    op.drop_column("nfse_modelo", "base_padrao", schema=SCHEMA)
    op.drop_column("nfse_modelo", "percentual", schema=SCHEMA)
    op.drop_column("nfse_modelo", "tipo_valor", schema=SCHEMA)
