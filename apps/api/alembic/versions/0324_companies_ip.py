"""companies.ip: o IP de saída de cada empresa, e a trava de um por empresa

Eduardo (25/09/2026): "colocar um ip para cada empresa, e já verifique se não
tem nada duplicado". O marketplace liga contas diferentes que aparecem pelo
mesmo IP, então dois CNPJs no mesmo IP é exatamente o que não pode acontecer.

Por que "IP" e não "proxy": nos perfis do AdsPower os proxies são fixos e o
endereço do proxy é o próprio IP que o marketplace enxerga (69 de 70 perfis).
O que precisa ser único é o IP; porta, usuário e senha do proxy continuam no
AdsPower.

A trava é um índice único parcial sobre o IP normalizado: vazio pode repetir
(empresa ainda sem IP), preenchido não. A validação de formato fica na API.

Revision ID: 0324_companies_ip
Revises: 0323_remove_vigia_chamados
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0324_companies_ip"
down_revision: str | None = "0323_remove_vigia_chamados"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    # A tabela é pequena (48 empresas) e a coluna nasce vazia, então as duas
    # operações são instantâneas. O risco é só ficar esperando atrás de uma
    # transação longa segurando companies: o limite faz a migração falhar
    # rápido e alto, em vez de travar o deploy e a API junto.
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.add_column("companies", sa.Column("ip", sa.Text(), nullable=True), schema=SCHEMA)
    op.execute(
        f"CREATE UNIQUE INDEX uq_companies_ip ON {SCHEMA}.companies (lower(btrim(ip))) "
        "WHERE ip IS NOT NULL AND btrim(ip) <> ''"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.uq_companies_ip")
    op.drop_column("companies", "ip", schema=SCHEMA)
