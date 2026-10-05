"""denuncia_robo_agenda: passo 6 "Réplica Denúncias Diversos" também às 19:00

Vinicius, 05/10/2026: com os "sem resposta (prazo vencido)" da Shopee a fila da réplica foi a 64
anúncios e cada conta manda no máximo 20 por hora — só às 15:00 levaria uns 3 dias. "Coloca 19:00
também". Só acrescenta se a agenda ainda está no padrão da 0368 (["15:00"]); mudada na aba Robô,
fica como está.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0370_denuncia_robo_agenda_replica_19h"
down_revision: str | None = "0369_denuncia_robo_agenda_tempo_parado"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE davinci.denuncia_robo_agenda SET horarios = '[\"15:00\", \"19:00\"]'::jsonb, "
        "atualizado_por = 'migração 0370' "
        "WHERE acao = 'replica_diversos' AND horarios = '[\"15:00\"]'::jsonb"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE davinci.denuncia_robo_agenda SET horarios = '[\"15:00\"]'::jsonb "
        "WHERE acao = 'replica_diversos' AND horarios = '[\"15:00\", \"19:00\"]'::jsonb"
    )
