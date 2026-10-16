"""Conciliação — modo do extrato por loja ("api" | "upload") e a loja Amazon **kfa** por
upload do CSV de Transações do Seller Central (16/10). A API da Amazon nega tudo pra KFA
(caso 21731531021); o arquivo "Visualizar transações › Fazer download" tem as mesmas
transações (status, grupo de pagamento, tipo, pedido, produto, promoção, tarifas, outros,
total). DOWNGRADE: tira a loja e a coluna.

Revision ID: 0412_conciliacao_kfa_upload
Revises: 0411_denuncia_compra_rastreio
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0412_conciliacao_kfa_upload"
down_revision: str | None = "0411_denuncia_compra_rastreio"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conciliacao_lojas",
        sa.Column("modo_extrato", sa.String(20), nullable=False, server_default="api"),
        schema="davinci",
    )
    bind = op.get_bind()
    achou = bind.execute(
        sa.text(
            """
            INSERT INTO davinci.conciliacao_lojas
                   (id, integration_id, platform, conta, ativa, inicio, modo_extrato)
            SELECT gen_random_uuid(), i.id, i.platform, 'kfa (amazon)', true,
                   DATE '2026-10-01', 'upload'
              FROM davinci.integrations i
             WHERE i.platform = 'amazon'
               AND lower(trim(i.name)) = 'kfa'
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
    print(f"0412_conciliacao_kfa_upload: loja ligada = {achou or 'NENHUMA'}")


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM davinci.conciliacao_lojas
             WHERE platform = 'amazon' AND modo_extrato = 'upload' AND conta = 'kfa (amazon)'
            """
        )
    )
    op.drop_column("conciliacao_lojas", "modo_extrato", schema="davinci")
