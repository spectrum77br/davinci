"""Ouvidoria — cadastra o Vigia de Conciliação com aviso Threema só pro Cairo (15/10/2026).

Vinicius: "pode criar na lista do robô e deixa somente para enviar ao cairo". A linha
nasce aqui (o catálogo em `ouvidoria.ROBOS` só completa nome/descrição) com o
destinatário = o Threema do usuário "cairo…" de Admin › Usuários. Se esse usuário
não tiver Threema cadastrado, o robô nasce `silencioso` (registra, não avisa) — nunca
cai na lista geral. DOWNGRADE: tira o robô (ocorrências e rodadas vão junto, FK).

Revision ID: 0401_vigia_conciliacao
Revises: 0400_conciliacao_shopee_jlas
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0401_vigia_conciliacao"
down_revision: str | None = "0400_conciliacao_shopee_jlas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    feito = bind.execute(
        sa.text(
            """
            INSERT INTO davinci.ouvidoria_robos
                   (chave, nome, descricao, area, cadencia_texto, plataformas, modo,
                    threema_recipients, reaviso_horas, config, created_at, updated_at)
            SELECT 'vigia_conciliacao',
                   'Vigia de Conciliação',
                   'O "Onde agir hoje" do painel Conciliação, uma vez por dia.',
                   'margem',
                   '1× por dia (07:00)',
                   '["ml", "shopee"]'::jsonb,
                   CASE WHEN u.threema IS NULL THEN 'silencioso' ELSE 'ligado' END,
                   u.threema,
                   24,
                   '{}'::jsonb,
                   now(), now()
              FROM (SELECT 1) AS um
              LEFT JOIN LATERAL (
                   SELECT threema FROM davinci.users
                    WHERE lower(coalesce(name, '')) LIKE 'cairo%'
                      AND threema IS NOT NULL AND trim(threema) <> ''
                    ORDER BY created_at LIMIT 1
              ) u ON true
            ON CONFLICT (chave) DO UPDATE
               SET threema_recipients = EXCLUDED.threema_recipients,
                   modo = EXCLUDED.modo
            RETURNING modo, threema_recipients
            """
        )
    ).fetchall()
    print(f"0401_vigia_conciliacao: robô cadastrado = {feito}")


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM davinci.ouvidoria_robos WHERE chave = 'vigia_conciliacao'"))
