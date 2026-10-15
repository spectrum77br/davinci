"""Conciliação — liga todas as lojas ML, Shopee e TikTok ativas desde 01/10/2026 (15/10).

OK do Vinicius ("pode seguir, faz os ajustes e liga as lojas"). Nome de conta que existe
em mais de uma plataforma (poofy, kia, jlas, mini, kfa…) ganha o sufixo " (plataforma)",
porque a tela e os links identificam a loja pelo nome — inclusive nas já ligadas.
Amazon fica como está (kia e poofy ligadas; kfa por upload; nexus parada).
DOWNGRADE: tira as lojas ligadas aqui e devolve os nomes antigos.

Revision ID: 0408_conciliacao_todas_as_lojas
Revises: 0407_conciliacao_fretes_pagos
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0408_conciliacao_todas_as_lojas"
down_revision: str | None = "0407_conciliacao_fretes_pagos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    renomeadas = bind.execute(
        sa.text(
            """
    WITH ativas AS (
        SELECT i.id, i.platform, trim(i.name) AS nome, lower(trim(i.name)) AS chave
          FROM davinci.integrations i
         WHERE i.archived_at IS NULL AND i.status = 'active'
           AND i.platform IN ('ml', 'shopee', 'tiktok', 'amazon')
    ), repetidas AS (
        SELECT chave FROM ativas GROUP BY chave HAVING count(DISTINCT platform) > 1
    )
            UPDATE davinci.conciliacao_lojas c
               SET conta = a.nome || ' (' || a.platform::text || ')'
              FROM ativas a
             WHERE a.id = c.integration_id
               AND a.chave IN (SELECT chave FROM repetidas)
               AND c.conta <> a.nome || ' (' || a.platform::text || ')'
            RETURNING c.conta
            """
        )
    ).fetchall()
    ligadas = bind.execute(
        sa.text(
            """
    WITH ativas AS (
        SELECT i.id, i.platform, trim(i.name) AS nome, lower(trim(i.name)) AS chave
          FROM davinci.integrations i
         WHERE i.archived_at IS NULL AND i.status = 'active'
           AND i.platform IN ('ml', 'shopee', 'tiktok', 'amazon')
    ), repetidas AS (
        SELECT chave FROM ativas GROUP BY chave HAVING count(DISTINCT platform) > 1
    )
            INSERT INTO davinci.conciliacao_lojas
                   (id, integration_id, platform, conta, ativa, inicio)
            SELECT gen_random_uuid(), a.id, a.platform,
                   CASE WHEN a.chave IN (SELECT chave FROM repetidas)
                        THEN a.nome || ' (' || a.platform::text || ')' ELSE a.nome END,
                   true, DATE '2026-10-01'
              FROM ativas a
             WHERE a.platform IN ('ml', 'shopee', 'tiktok')
               AND NOT EXISTS (
                   SELECT 1 FROM davinci.conciliacao_lojas c WHERE c.integration_id = a.id
               )
             ORDER BY a.platform, a.chave
            RETURNING conta
            """
        )
    ).fetchall()
    print(
        "0408_conciliacao_todas_as_lojas: renomeadas="
        f"{[r[0] for r in renomeadas]} ligadas={[r[0] for r in ligadas]}"
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            DELETE FROM davinci.conciliacao_lojas
             WHERE inicio = DATE '2026-10-01' AND platform IN ('ml', 'shopee', 'tiktok')
               AND conta NOT IN ('jlas2', 'Jlas (shopee)', 'Mini (tiktok)')
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE davinci.conciliacao_lojas c SET conta = trim(i.name)
              FROM davinci.integrations i WHERE i.id = c.integration_id
            """
        )
    )
