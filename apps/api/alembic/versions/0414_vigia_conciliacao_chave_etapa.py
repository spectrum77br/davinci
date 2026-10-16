"""Vigia de Conciliação — a chave da ocorrência de "pedidos" passou a levar a etapa
(16/10, lote 4: o filtro do "Onde agir" manda `etapa=pendencia,reaberto`). Sem migrar as
chaves gravadas, a 1ª rodada das 07:00 fecharia todas como "sumiu" e reabriria como
novas, e o Threema re-avisaria tudo. DOWNGRADE: volta a chave antiga.

Revision ID: 0414_vigia_conciliacao_chave_etapa
Revises: 0413_conciliacao_dia_reaberto
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0414_vigia_conciliacao_chave_etapa"
down_revision: str | None = "0413_conciliacao_dia_reaberto"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOVA = ":pedidos:etapa=pendencia,reaberto,situacao="
_ANTIGA = ":pedidos:situacao="


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE ouvidoria_ocorrencias SET chave = replace(chave, :antiga, :nova) "
            "WHERE robo_chave = 'vigia_conciliacao' AND chave LIKE :padrao"
        ).bindparams(antiga=_ANTIGA, nova=_NOVA, padrao=f"%{_ANTIGA}%")
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE ouvidoria_ocorrencias SET chave = replace(chave, :nova, :antiga) "
            "WHERE robo_chave = 'vigia_conciliacao' AND chave LIKE :padrao"
        ).bindparams(antiga=_ANTIGA, nova=_NOVA, padrao=f"%{_NOVA}%")
    )
