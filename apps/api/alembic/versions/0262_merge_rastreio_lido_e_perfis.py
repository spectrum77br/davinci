# ruff: noqa: E501
"""Junta os dois ramos de migração que nasceram em paralelo (10/09/2026).

Duas sessões trabalharam no mesmo repositório ao mesmo tempo: uma criou
0256_logistica_rastreio_lido_em em cima de 0255 e a outra criou a corrente
0257→0261 também em cima de 0255. O banco ficou com DUAS cabeças, e o
`alembic upgrade head` do deploy aplicou só uma delas — a coluna
`logistica.rastreio_lido_em` ficou faltando com o código novo já no ar, e a
aba Logística quebrou por alguns minutos até a migração ser aplicada na mão.

Este merge não muda nada no banco; só volta a existir UMA cabeça, pra que o
`upgrade head` do próximo deploy aplique tudo de novo sem escolher lado.

Revision ID: 0262_merge_rastreio_lido_e_perfis
Revises: 0256_logistica_rastreio_lido_em, 0261_vw_perfis_prefere_israel
Create Date: 2026-09-10
"""

from collections.abc import Sequence

revision: str = "0262_merge_rastreio_lido_e_perfis"
down_revision: tuple[str, ...] = (
    "0256_logistica_rastreio_lido_em",
    "0261_vw_perfis_prefere_israel",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Merge puro: nada a fazer."""


def downgrade() -> None:
    """Merge puro: nada a fazer."""
