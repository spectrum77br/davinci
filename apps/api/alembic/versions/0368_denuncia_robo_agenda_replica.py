"""denuncia_robo_agenda: passo 6 "Réplica Denúncias Diversos" ligado às 15:00

Vinicius, 05/10/2026: "vamos criar um passo que chama Réplica Denúncias Diversos… pega os anúncios
que já denunciamos no passo 7 [Diversos]… a loja já deu como improcedente… duas empresas novas… vai
no marketplace e abre uma denúncia… falando que já tem reclamação na Anatel protocolo xxxxx".
Começa ligado às 15:00 (o robô está parado entre a Anatel da manhã e a checagem das 16:30); muda na
aba Robô.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0368_denuncia_robo_agenda_replica"
down_revision: str | None = "0367_flex"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO davinci.denuncia_robo_agenda (acao, ligado, horarios, atualizado_por) "
        "VALUES ('replica_diversos', true, '[\"15:00\"]'::jsonb, 'migração 0368') "
        "ON CONFLICT (acao) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM davinci.denuncia_robo_agenda WHERE acao = 'replica_diversos'")
