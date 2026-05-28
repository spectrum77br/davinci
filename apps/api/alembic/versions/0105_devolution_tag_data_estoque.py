"""devolution tag + data_devolvido_estoque

Revision ID: 0105
Revises: 0104
Create Date: 2026-05-28

Adiciona 2 colunas à tabela devolutions:

  * tag                    — tags de sufixo regional dos SKUs do pedido
                             (`.sp`, `.ra`, `.pi`, …). Para SKU composto
                             (`a.ra+b.pi`) concatena os sufixos: `.ra.pi`.
                             Backfill a partir do `sku` das linhas existentes.
  * data_devolvido_estoque — timestamp setado quando o toggle "devolver
                             estoque" passa a TRUE (auto, via router).
"""

from alembic import op
import sqlalchemy as sa

revision = "0105_devolution_tag_data_estoque"
down_revision = "0104_devolution_troca_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("devolutions", sa.Column("tag", sa.Text(), nullable=True))
    op.add_column(
        "devolutions",
        sa.Column("data_devolvido_estoque", sa.DateTime(timezone=True), nullable=True),
    )

    # Backfill `tag` a partir do sufixo regional de cada componente do SKU.
    op.execute(
        r"""
        UPDATE devolutions d
        SET tag = sub.tag
        FROM (
            SELECT dd.id,
                   NULLIF(
                       string_agg(
                           CASE
                               WHEN lower((regexp_match(part, '\.([A-Za-z]+)$'))[1])
                                    IN ('ci', 'pi', 'ra', 'sa', 'sp', 'us', 'cd')
                               THEN '.' || lower((regexp_match(part, '\.([A-Za-z]+)$'))[1])
                               ELSE ''
                           END,
                           '' ORDER BY ord
                       ),
                       ''
                   ) AS tag
            FROM devolutions dd
            CROSS JOIN LATERAL unnest(string_to_array(dd.sku, '+'))
                WITH ORDINALITY AS u(part, ord)
            WHERE dd.sku IS NOT NULL AND btrim(dd.sku) <> ''
            GROUP BY dd.id
        ) sub
        WHERE d.id = sub.id
        """
    )


def downgrade() -> None:
    op.drop_column("devolutions", "data_devolvido_estoque")
    op.drop_column("devolutions", "tag")
