"""Conciliação TikTok — o dia do extrato passa a ser o dia em que o repasse cai no banco.

15/10: as linhas da TikTok estavam na hora do `statement_time` (meia-noite UTC =
21:00 BRT da véspera), então o "extrato de ontem" ficava vazio até a TikTok
publicar o próximo. Agora a âncora é 00:00 BRT da data UTC do extrato (= +3 h).
Desloca as linhas já gravadas e reabre os dias que o robô fechou (ele fecha de
novo na próxima rodada, já com as linhas no dia certo). DOWNGRADE: volta 3 h.

Revision ID: 0403_conciliacao_tiktok_dia_do_banco
Revises: 0402_conciliacao_tiktok_mini
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0403_conciliacao_tiktok_dia_do_banco"
down_revision: str | None = "0402_conciliacao_tiktok_mini"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    n = bind.execute(
        sa.text(
            """
            UPDATE davinci.conciliacao_movimentos
               SET data = data + interval '3 hours'
             WHERE origem = 'tiktok_extrato'
            """
        )
    ).rowcount
    m = bind.execute(
        sa.text(
            """
            DELETE FROM davinci.conciliacao_dias d
             USING davinci.conciliacao_lojas l
             WHERE l.integration_id = d.integration_id
               AND l.platform = 'tiktok'
               AND d.fechado_por = 'robô'
            """
        )
    ).rowcount
    print(f"0403_conciliacao_tiktok_dia_do_banco: {n} linhas deslocadas, {m} dias reabertos")


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE davinci.conciliacao_movimentos
               SET data = data - interval '3 hours'
             WHERE origem = 'tiktok_extrato'
            """
        )
    )
