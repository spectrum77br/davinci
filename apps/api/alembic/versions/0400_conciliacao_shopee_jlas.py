"""Conciliação — liga a loja Shopee **Jlas** com início em 01/10/2026 (passo 3, 15/10).

OK do Vinicius em 15/10 ("tem meu ok vamos fazer ... pegamos do dia 01"). A Shopee
não precisa de configuração nova: a carteira é lida com a chave que já existe.
Se não achar a integração, só avisa (print) e segue. DOWNGRADE: tira a loja.

Revision ID: 0400_conciliacao_shopee_jlas
Revises: 0399_atendimento_visto_em
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0400_conciliacao_shopee_jlas"
down_revision: str | None = "0399_atendimento_visto_em"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    bind = op.get_bind()
    achou = bind.execute(
        sa.text(
            """
            INSERT INTO davinci.conciliacao_lojas
                   (id, integration_id, platform, conta, ativa, inicio)
            SELECT gen_random_uuid(), i.id, i.platform, i.name, true, DATE '2026-10-01'
              FROM davinci.integrations i
             WHERE i.platform = 'shopee'
               AND lower(trim(i.name)) = 'jlas'
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
    nada = "NENHUMA (Shopee Jlas não encontrada)"
    print(f"0400_conciliacao_shopee_jlas: loja ligada = {achou or nada}")


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM davinci.conciliacao_lojas
             WHERE platform = 'shopee' AND lower(trim(conta)) = 'jlas'
            """
        )
    )
