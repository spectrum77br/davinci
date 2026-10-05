"""denuncia_robo_agenda: passo 8 "Tempo parado" de hora em hora, 12:00 às 16:00

Vinicius, 05/10/2026: em vez de rodar 0→9 sem parar ("finalizou já começa a rodar de novo"), manter os
horários e aproveitar o tempo parado do perfil 50 com os prints atrasados e a conferência de
ativos/inativos pela metade. O passo desiste sozinho se o próximo passo do perfil 50 vem em menos de
30 min; os horários cobrem o buraco entre a Anatel da manhã e a checagem das 16:30. Muda na aba Robô.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0369_denuncia_robo_agenda_tempo_parado"
down_revision: str | None = "0368_denuncia_robo_agenda_replica"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO davinci.denuncia_robo_agenda (acao, ligado, horarios, atualizado_por) "
        "VALUES ('aproveitar_parado', true, "
        "'[\"12:00\", \"13:00\", \"14:00\", \"15:00\", \"16:00\"]'::jsonb, 'migração 0369') "
        "ON CONFLICT (acao) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM davinci.denuncia_robo_agenda WHERE acao = 'aproveitar_parado'")
