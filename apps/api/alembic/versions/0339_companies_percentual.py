"""Empresas: a porcentagem de cada empresa (% padrão da nota de serviço)

Eduardo (29/09/2026): "em cadastros na aba empresas, precisamos colocar uma
nova coluna, porcentagem, que vai ser a porcentagem de cada empresa que temos".
A porcentagem da empresa passa a ser o % PADRÃO das notas de serviço (NFS-e)
de percentual que ELA emite:

- `companies.percentual_servico`: 0.5 = 0,5%. NULL = a empresa não tem %.
- `nfse_modelo`: a nota fixa de percentual pode ficar SEM % próprio (NULL = usa
  a % da empresa na hora de emitir). A trava `ck_nfse_modelo_valor_tipo` da
  0338 exigia o % e passa a exigir só o valor da nota fixa de valor fixo.

Espelha `app/models/company.py` e `app/models/nfse.py`.

Revision ID: 0339_companies_percentual
Revises: 0338_nfse_percentual
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0339_companies_percentual"
down_revision: str | None = "0338_nfse_percentual"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

# Nome real no banco (0338 criou com op.f; conferido no banco local).
CK_MODELO = "ck_nfse_modelo_valor_tipo"
CK_EMPRESA = "ck_companies_percentual_servico"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")

    op.add_column(
        "companies",
        sa.Column("percentual_servico", sa.Numeric(9, 4), nullable=True),
        schema=SCHEMA,
    )
    # op.f(): o nome já vem pronto (sem ele a naming convention prefixa de novo).
    op.create_check_constraint(
        op.f(CK_EMPRESA),
        "companies",
        "percentual_servico IS NULL OR (percentual_servico > 0 AND percentual_servico <= 100)",
        schema=SCHEMA,
    )

    # Nota fixa de percentual sem % próprio = usa a % da empresa.
    op.drop_constraint(op.f(CK_MODELO), "nfse_modelo", type_="check", schema=SCHEMA)
    op.create_check_constraint(
        op.f(CK_MODELO),
        "nfse_modelo",
        "(tipo_valor = 'fixo' AND valor IS NOT NULL) OR tipo_valor = 'percentual'",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")

    # A nota fixa que usava a % da empresa volta com a % da empresa gravada nela
    # (a trava antiga exige o % na própria nota).
    op.execute(
        "UPDATE davinci.nfse_modelo m SET percentual = c.percentual_servico"
        " FROM davinci.companies c"
        " WHERE m.company_id = c.id AND m.tipo_valor = 'percentual'"
        " AND m.percentual IS NULL AND c.percentual_servico IS NOT NULL"
    )
    op.drop_constraint(op.f(CK_MODELO), "nfse_modelo", type_="check", schema=SCHEMA)
    # NOT VALID: se sobrou nota fixa sem % (empresa sem % também), ela fica como
    # está em vez de travar o downgrade; toda gravação nova já passa pela trava.
    op.execute(
        "ALTER TABLE davinci.nfse_modelo ADD CONSTRAINT ck_nfse_modelo_valor_tipo CHECK ("
        "(tipo_valor = 'fixo' AND valor IS NOT NULL)"
        " OR (tipo_valor = 'percentual' AND percentual IS NOT NULL)"
        ") NOT VALID"
    )
    # Não sobrou nenhuma: a trava volta inteira, como a 0338 deixou.
    op.execute(
        "DO $$ BEGIN"
        " IF NOT EXISTS (SELECT 1 FROM davinci.nfse_modelo"
        "  WHERE tipo_valor = 'percentual' AND percentual IS NULL) THEN"
        "  ALTER TABLE davinci.nfse_modelo VALIDATE CONSTRAINT ck_nfse_modelo_valor_tipo;"
        " END IF;"
        " END $$"
    )

    op.drop_constraint(op.f(CK_EMPRESA), "companies", type_="check", schema=SCHEMA)
    op.drop_column("companies", "percentual_servico", schema=SCHEMA)
