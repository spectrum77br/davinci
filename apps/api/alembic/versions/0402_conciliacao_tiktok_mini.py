"""Conciliação — liga a loja TikTok **Mini** com início em 01/10/2026 (passo 4, 15/10).

OK do Vinicius em 15/10 ("pode fazer o tiktok desde 01/10"). É a loja TikTok que mais
vende; o nome da integração é " Mini" (com espaço na frente) — por isso o
lower(trim), e a conta do painel fica sem o espaço. Se não achar, só avisa e segue.
DOWNGRADE: tira a loja (os movimentos ficam).

Revision ID: 0402_conciliacao_tiktok_mini
Revises: 0401_vigia_conciliacao
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0402_conciliacao_tiktok_mini"
down_revision: str | None = "0401_vigia_conciliacao"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    achou = bind.execute(
        sa.text(
            """
            INSERT INTO davinci.conciliacao_lojas
                   (id, integration_id, platform, conta, ativa, inicio)
            SELECT gen_random_uuid(), i.id, i.platform, trim(i.name), true, DATE '2026-10-01'
              FROM davinci.integrations i
             WHERE i.platform = 'tiktok'
               AND lower(trim(i.name)) = 'mini'
               AND i.archived_at IS NULL
               AND NOT EXISTS (
                   SELECT 1 FROM davinci.conciliacao_lojas c WHERE c.integration_id = i.id
               )
             ORDER BY (i.status = 'active') DESC, i.created_at
             LIMIT 1
            RETURNING conta, integration_id
            """
        )
    ).fetchall()
    nada = "NENHUMA (TikTok Mini não encontrada)"
    print(f"0402_conciliacao_tiktok_mini: loja ligada = {achou or nada}")


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM davinci.conciliacao_lojas
             WHERE platform = 'tiktok' AND lower(trim(conta)) = 'mini'
            """
        )
    )
