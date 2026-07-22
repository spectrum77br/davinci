# ruff: noqa: E501
"""sales_team: recodifica equipes p/ o formato empresa.membro (E*100+M)

Antes o `sales_team` era um int achatado (1, 2, 3 = membros da única empresa 1,
exibidos como "1.1", "1.2", "1.3"). Pra suportar mais empresas (2.1, 2.2, ...)
o número passa a codificar empresa+membro num único int: E*100+M
(1.1 = 101, 2.1 = 201). Como todas as equipes de hoje são da empresa 1, a
conversão é somar 100 aos valores atuais (< 100).

Migra `store_info.sales_team` (int) e `users.sales_teams` (JSONB array de int).
Idempotente: só mexe em valores < 100 (equipes ainda não recodificadas).

Revision ID: 0192_sales_team_empresa_membro
Revises: 0191_logistica_threema_enviado_at
Create Date: 2026-07-22
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0192_sales_team_empresa_membro"
down_revision: str | None = "0191_logistica_threema_enviado_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    # store_info.sales_team: soma 100 nas equipes achatadas (empresa 1).
    op.execute(
        f"UPDATE {SCHEMA}.store_info SET sales_team = sales_team + 100 "
        "WHERE sales_team IS NOT NULL AND sales_team < 100"
    )
    # users.sales_teams: array JSONB de ints — recodifica cada elemento < 100.
    op.execute(
        f"""
        UPDATE {SCHEMA}.users
        SET sales_teams = (
            SELECT jsonb_agg(
                CASE WHEN (elem)::int < 100 THEN (elem)::int + 100 ELSE (elem)::int END
                ORDER BY (elem)::int
            )
            FROM jsonb_array_elements(sales_teams) AS elem
        )
        WHERE sales_teams IS NOT NULL AND jsonb_array_length(sales_teams) > 0
        """
    )


def downgrade() -> None:
    # Reverte só a faixa da empresa 1 (101..199 → 1..99).
    op.execute(
        f"UPDATE {SCHEMA}.store_info SET sales_team = sales_team - 100 "
        "WHERE sales_team IS NOT NULL AND sales_team > 100 AND sales_team < 200"
    )
    op.execute(
        f"""
        UPDATE {SCHEMA}.users
        SET sales_teams = (
            SELECT jsonb_agg(
                CASE WHEN (elem)::int > 100 AND (elem)::int < 200
                     THEN (elem)::int - 100 ELSE (elem)::int END
                ORDER BY (elem)::int
            )
            FROM jsonb_array_elements(sales_teams) AS elem
        )
        WHERE sales_teams IS NOT NULL AND jsonb_array_length(sales_teams) > 0
        """
    )
