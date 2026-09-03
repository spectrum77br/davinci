# ruff: noqa: E501
"""Devoluções: motivo "Mudou de ideia" passa a se chamar "Bloqueado" (Eduardo, 2026-09-03).

"mudou de ideia - bloqueado" (lista de motivos organizada de 03/09) — o front
já lista "Bloqueado"; esta migração renomeia as linhas antigas para a tela e
os filtros não mostrarem dois nomes para a mesma coisa. Só dados; nenhuma
coluna muda. Downgrade desfaz o rename.

Revision ID: 0239_devolucao_motivo_bloqueado
Revises: 0238_pricing_ean_1000
Create Date: 2026-09-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0239_devolucao_motivo_bloqueado"
down_revision: str | None = "0238_pricing_ean_1000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(
        "UPDATE devolutions SET motivo_devolucao = 'Bloqueado' "
        "WHERE lower(btrim(motivo_devolucao)) = 'mudou de ideia'"
    )


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(
        "UPDATE devolutions SET motivo_devolucao = 'Mudou de ideia' "
        "WHERE lower(btrim(motivo_devolucao)) = 'bloqueado'"
    )
