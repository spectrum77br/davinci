"""Equipe comercial separada dos códigos internos de acesso às lojas.

As lojas já vinculadas a um código de acesso começam na Equipe 1. Usuários
com códigos 101..199 pertencem à Equipe 1; 201..299, à Equipe 2. Outros
códigos não indicam equipe comercial, e vínculos nas duas faixas ficam sem
atribuição por serem ambíguos. Os códigos de acesso permanecem intactos.
"""
# ruff: noqa: S608
# Os identificadores SQL são constantes da migração, nunca entrada do usuário.

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0263_commercial_team"
down_revision: str | None = "0262_merge_perfis_rastreio"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    for table in ("users", "store_info"):
        op.add_column(
            table, sa.Column("commercial_team", sa.Integer(), nullable=True), schema=SCHEMA
        )
        op.create_check_constraint(
            op.f(f"ck_{table}_commercial_team_valid"),
            table,
            "commercial_team IN (1, 2)",
            schema=SCHEMA,
        )

    op.execute(
        f"UPDATE {SCHEMA}.store_info SET commercial_team = 1 WHERE sales_team IS NOT NULL"
    )
    op.execute(
        f"""
        WITH classified AS (
            SELECT u.id,
                   CASE
                       WHEN code ~ '^1[0-9]{{2}}$' AND code <> '100' THEN 1
                       WHEN code ~ '^2[0-9]{{2}}$' AND code <> '200' THEN 2
                   END AS team
            FROM {SCHEMA}.users u
            CROSS JOIN LATERAL jsonb_array_elements_text(
                CASE WHEN jsonb_typeof(u.sales_teams) = 'array'
                     THEN u.sales_teams ELSE '[]'::jsonb END
            ) AS codes(code)
        ), assignments AS (
            SELECT id, MIN(team) AS team
            FROM classified
            GROUP BY id
            HAVING COUNT(DISTINCT team) = 1
        )
        UPDATE {SCHEMA}.users u
        SET commercial_team = a.team
        FROM assignments a
        WHERE u.id = a.id
        """
    )


def downgrade() -> None:
    for table in ("store_info", "users"):
        op.drop_constraint(op.f(f"ck_{table}_commercial_team_valid"), table, schema=SCHEMA)
        op.drop_column(table, "commercial_team", schema=SCHEMA)
