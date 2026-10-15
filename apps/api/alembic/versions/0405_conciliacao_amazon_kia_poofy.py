"""Conciliação — liga as lojas Amazon **kia** e **poofy** com início em 01/10/2026 (passo 5, 15/10).

OK do Vinicius em 15/10 ("pode começar pela kia e poofy desde 01/10"). A kfa fica de
fora (API bloqueada pela Amazon, caso 21731531021) até entrar por upload.
DOWNGRADE: tira as duas lojas.

Revision ID: 0405_conciliacao_amazon_kia_poofy
Revises: 0404_conciliacao_venda_afiliados
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0405_conciliacao_amazon_kia_poofy"
down_revision: str | None = "0404_conciliacao_venda_afiliados"
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
             WHERE i.platform = 'amazon'
               AND lower(trim(i.name)) IN ('kia', 'poofy')
               AND i.archived_at IS NULL
               AND i.status = 'active'
               AND NOT EXISTS (
                   SELECT 1 FROM davinci.conciliacao_lojas c WHERE c.integration_id = i.id
               )
            RETURNING conta, integration_id
            """
        )
    ).fetchall()
    print(f"0405_conciliacao_amazon_kia_poofy: lojas ligadas = {achou or 'NENHUMA'}")


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM davinci.conciliacao_lojas
             WHERE platform = 'amazon' AND lower(trim(conta)) IN ('kia', 'poofy')
            """
        )
    )
